#!/usr/bin/env python3
"""Node dial tester (vless / hysteria2 / tuic / ss).

For each URI it launches xray (vless/ss) or sing-box (hysteria2/tuic) with a
local SOCKS5 on 127.0.0.1:PORT, then performs real proxied checks:

  1. http://www.gstatic.com/generate_204   -> expect HTTP 204
  2. https://www.cloudflare.com/cdn-cgi/trace -> parse ip= / loc=

Two rounds (default 30-45s apart). A node is usable iff every round succeeds
and every RTT < 800ms. Output: JSON list of
  {uri, ok, rtt_ms, exit_ip, loc, err}

Options:
  --via-connect-proxy PROXY   route the node's TCP dial through an upstream
                              HTTP CONNECT proxy (for hosts whose direct
                              egress is blocked). UDP protocols (hy2/tuic)
                              cannot use this mode.
  --target204 URL / --target-trace URL   override check targets (self-test).
  --interval-min/--interval-max SEC       gap between rounds (default 30/45).
  --rounds N  --timeout SEC  --port PORT  --out FILE
"""
import argparse, base64, json, os, random, re, select, shutil, socket
import subprocess, sys, tempfile, threading, time, urllib.parse

BIN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin")
XRAY = os.path.join(BIN_DIR, "xray")
SINGBOX = os.path.join(BIN_DIR, "sing-box")

DEF_204 = "http://www.gstatic.com/generate_204"
DEF_TRACE = "https://www.cloudflare.com/cdn-cgi/trace"
RTT_LIMIT_MS = 800


# ---------------- URI parsing ----------------
def _q(uri):
    p = urllib.parse.urlsplit(uri)
    return p, dict(urllib.parse.parse_qsl(p.query, keep_blank_values=True))

def _un(s):
    return urllib.parse.unquote(s or "")

def parse_uri(uri):
    p, q = _q(uri)
    scheme = p.scheme.lower()
    if scheme == "vless":
        host = p.hostname or ""
        port = p.port or 443
        return {"proto": "vless", "id": _un(p.username), "host": host,
                "port": port, "params": {k: _un(v) for k, v in q.items()},
                "remark": _un(p.fragment)}
    if scheme == "hysteria2":
        host = p.hostname or ""
        port = p.port or 443
        return {"proto": "hysteria2", "password": _un(p.username),
                "host": host, "port": port,
                "params": {k: _un(v) for k, v in q.items()},
                "remark": _un(p.fragment)}
    if scheme == "tuic":
        host = p.hostname or ""
        port = p.port or 443
        return {"proto": "tuic", "uuid": _un(p.username),
                "password": _un(p.password), "host": host, "port": port,
                "params": {k: _un(v) for k, v in q.items()},
                "remark": _un(p.fragment)}
    if scheme in ("ss", "shadowsocks"):
        body = uri.split("://", 1)[1].split("#", 1)[0].split("?", 1)[0]
        remark = _un(uri.split("#", 1)[1]) if "#" in uri else ""
        method, password, host, port = None, None, None, None
        if "@" in body:
            left, right = body.rsplit("@", 1)
            try:
                left = base64.b64decode(left + "=" * (-len(left) % 4)).decode()
            except Exception:
                pass
            if ":" in left:
                method, password = left.split(":", 1)
            hostport = right
            if ":" in hostport:
                host, port = hostport.rsplit(":", 1)
                port = int(port)
        else:
            try:
                dec = base64.b64decode(body + "=" * (-len(body) % 4)).decode()
            except Exception:
                raise ValueError("bad ss uri")
            m = re.match(r"([^:]+):([^@]+)@([^:]+):(\d+)", dec)
            if not m:
                raise ValueError("bad ss uri")
            method, password, host, port = m.group(1), m.group(2), m.group(3), int(m.group(4))
        if not all([method, password, host, port]):
            raise ValueError("bad ss uri")
        return {"proto": "ss", "method": method, "password": password,
                "host": host, "port": port, "remark": remark, "params": {}}
    raise ValueError(f"unsupported scheme: {scheme}")


# ---------------- config builders ----------------
def _bool(v):
    return str(v).lower() in ("1", "true", "yes")

def build_xray_config(node, socks_port):
    prm = node["params"]
    security = prm.get("security", "none").lower()
    network = prm.get("type", "tcp").lower()
    stream = {"network": network}
    if security == "reality":
        stream["security"] = "reality"
        stream["realitySettings"] = {
            "show": False,
            "fingerprint": prm.get("fp", "chrome"),
            "serverName": prm.get("sni", node["host"]),
            "publicKey": prm.get("pbk", ""),
            "shortId": prm.get("sid", ""),
            "spiderX": prm.get("spx", "/"),
        }
    elif security == "tls":
        stream["security"] = "tls"
        tls = {"serverName": prm.get("sni", node["host"]),
               "fingerprint": prm.get("fp", "chrome"),
               "allowInsecure": _bool(prm.get("allowInsecure", prm.get("insecure", "0")))}
        if prm.get("alpn"):
            tls["alpn"] = [a.strip() for a in prm["alpn"].split(",")]
        stream["tlsSettings"] = tls
    if network == "ws":
        stream["wsSettings"] = {
            "path": prm.get("path", "/"),
            "headers": {"Host": prm.get("host", prm.get("sni", node["host"]))},
        }
    elif network == "grpc":
        stream["grpcSettings"] = {
            "serviceName": prm.get("serviceName", ""),
            "multiMode": prm.get("mode", "gun") == "multi",
        }
    if node["proto"] == "vless":
        user = {"id": node["id"], "encryption": "none"}
        if prm.get("flow"):
            user["flow"] = prm["flow"]
        outbound = {"protocol": "vless",
                    "settings": {"vnext": [{"address": node["dial_host"],
                                            "port": node["dial_port"],
                                            "users": [user]}]},
                    "streamSettings": stream}
    else:  # ss
        outbound = {"protocol": "shadowsocks",
                    "settings": {"servers": [{
                        "address": node["dial_host"], "port": node["dial_port"],
                        "method": node["method"], "password": node["password"],
                        "uot": False}]}}
    return {"log": {"loglevel": "warning"},
            "inbounds": [{"port": socks_port, "listen": "127.0.0.1",
                          "protocol": "socks",
                          "settings": {"auth": "noauth", "udp": True}}],
            "outbounds": [outbound]}

def build_singbox_config(node, socks_port):
    prm = node["params"]
    insecure = _bool(prm.get("insecure", prm.get("allowInsecure", "0")))
    tls = {"enabled": True, "server_name": prm.get("sni", node["host"]),
           "insecure": insecure}
    if node["proto"] == "hysteria2":
        ob = {"type": "hysteria2", "tag": "proxy",
              "server": node["dial_host"], "server_port": node["dial_port"],
              "password": node["password"], "tls": tls}
        if prm.get("obfs"):
            ob["obfs"] = {"type": prm["obfs"],
                          "password": prm.get("obfs-password", "")}
    elif node["proto"] == "tuic":
        tls = dict(tls)
        if prm.get("alpn"):
            tls["alpn"] = prm["alpn"]
        ob = {"type": "tuic", "tag": "proxy",
              "server": node["dial_host"], "server_port": node["dial_port"],
              "uuid": node["uuid"], "password": node["password"], "tls": tls,
              "congestion_control": prm.get("congestion_control", "cubic"),
              "udp_relay_mode": prm.get("udp_relay_mode", "native")}
    else:
        raise ValueError("sing-box only handles hysteria2/tuic here")
    return {"log": {"level": "warn"},
            "inbounds": [{"type": "mixed", "tag": "in",
                          "listen": "127.0.0.1", "listen_port": socks_port}],
            "outbounds": [ob]}


# ---------------- CONNECT relay (TCP via upstream HTTP proxy) ----------------
class ConnectRelay:
    """Listen locally; forward each TCP conn to (host,port) via HTTP CONNECT."""
    def __init__(self, proxy_url, target_host, target_port):
        u = urllib.parse.urlsplit(proxy_url)
        self.phost, self.pport = u.hostname, u.port or 8080
        user, pwd = u.username, u.password
        if not user:
            # fall back to env proxy credentials for the same host (never logged)
            import os as _os
            for _k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
                _v = _os.environ.get(_k, "")
                try:
                    _eu = urllib.parse.urlsplit(_v)
                except Exception:
                    continue
                if _eu.hostname == self.phost and _eu.username:
                    user, pwd = _eu.username, _eu.password
                    break
        self.pauth = None
        if user:
            import base64 as _b64
            self.pauth = _b64.b64encode(
                f"{user}:{pwd or ''}".encode()).decode()
        self.thost, self.tport = target_host, target_port
        self.ls = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.ls.bind(("127.0.0.1", 0))
        self.ls.listen(64)
        self.port = self.ls.getsockname()[1]
        self._stop = False
        self.t = threading.Thread(target=self._serve, daemon=True)
        self.t.start()

    def _serve(self):
        while not self._stop:
            try:
                c, _ = self.ls.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(c,), daemon=True).start()

    def _handle(self, c):
        try:
            p = socket.create_connection((self.phost, self.pport), timeout=15)
            req = (f"CONNECT {self.thost}:{self.tport} HTTP/1.1\r\n"
                   f"Host: {self.thost}:{self.tport}\r\n")
            if self.pauth:
                req += f"Proxy-Authorization: Basic {self.pauth}\r\n"
            req += "\r\n"
            p.sendall(req.encode())
            buf = b""
            while b"\r\n\r\n" not in buf:
                chunk = p.recv(4096)
                if not chunk:
                    raise ConnectionError("proxy closed")
                buf += chunk
            if b" 200 " not in buf.split(b"\r\n", 1)[0]:
                raise ConnectionError("CONNECT refused: " + buf[:80].decode("latin1"))
            for s in (c, p):
                s.setblocking(False)
            while True:
                r, _, _ = select.select([c, p], [], [], 120)
                if not r:
                    break
                for s in r:
                    d = s.recv(65536)
                    if not d:
                        return
                    (p if s is c else c).sendall(d)
        except Exception:
            pass
        finally:
            try: c.close()
            except Exception: pass

    def close(self):
        self._stop = True
        try: self.ls.close()
        except Exception: pass


# ---------------- dial runner ----------------
def wait_port(port, timeout=12):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
            return True
        except OSError:
            time.sleep(0.3)
    return False

def curl_via_socks(socks_port, url, timeout):
    # NOTE: do NOT add --noproxy '*': it disables the explicit SOCKS proxy too.
    cmd = ["curl", "-s", "--max-time", str(timeout),
           "--socks5-hostname", f"127.0.0.1:{socks_port}",
           "-o", "/dev/null", "-w", "%{http_code} %{time_total}", url]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 10)
    code, _, t = r.stdout.strip().partition(" ")
    return code.strip(), (float(t) * 1000 if t else None)

def curl_body_via_socks(socks_port, url, timeout):
    cmd = ["curl", "-s", "--max-time", str(timeout),
           "--socks5-hostname", f"127.0.0.1:{socks_port}",
           "-w", "\n%{http_code} %{time_total}", url]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 10)
    out = r.stdout.rsplit("\n", 1)
    body, tail = (out[0], out[1]) if len(out) == 2 else ("", out[0])
    code, _, t = tail.strip().partition(" ")
    return body, code.strip(), (float(t) * 1000 if t else None)

def parse_trace(body):
    ip = loc = None
    for line in body.splitlines():
        if line.startswith("ip="): ip = line[3:].strip()
        elif line.startswith("loc="): loc = line[4:].strip()
    return ip, loc

def dial_once(node, socks_port, target204, target_trace, timeout):
    code, rtt = curl_via_socks(socks_port, target204, timeout)
    if code != "204" or rtt is None:
        return False, None, None, None, f"generate_204 -> http {code}"
    body, tcode, _ = curl_body_via_socks(socks_port, target_trace, timeout)
    ip, loc = parse_trace(body)
    if tcode != "200" or not ip:
        return False, rtt, None, None, f"cf trace -> http {tcode}"
    return True, rtt, ip, loc, None

def test_node(uri, args, socks_port):
    res = {"uri": uri, "ok": False, "rtt_ms": None,
           "exit_ip": None, "loc": None, "err": None}
    relay = None
    try:
        node = parse_uri(uri)
    except Exception as e:
        res["err"] = f"parse: {e}"
        return res
    node["dial_host"], node["dial_port"] = node["host"], node["port"]
    if args.via_connect_proxy:
        if node["proto"] in ("hysteria2", "tuic"):
            res["err"] = "via-connect-proxy is TCP-only; hy2/tuic need UDP"
            return res
        relay = ConnectRelay(args.via_connect_proxy, node["host"], node["port"])
        node["dial_host"], node["dial_port"] = "127.0.0.1", relay.port
    try:
        if node["proto"] in ("vless", "ss"):
            exe, cfg = XRAY, build_xray_config(node, socks_port)
        else:
            exe, cfg = SINGBOX, build_singbox_config(node, socks_port)
        if not os.path.exists(exe):
            raise RuntimeError(f"missing binary: {exe}")
        tmp = tempfile.mkdtemp(prefix="dialtest_")
        cfg_path = os.path.join(tmp, "config.json")
        with open(cfg_path, "w") as f:
            json.dump(cfg, f)
        proc = subprocess.Popen([exe, "run", "-c", cfg_path] if exe == XRAY
                                else [exe, "run", "-c", cfg_path],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        try:
            if not wait_port(socks_port):
                raise RuntimeError("local proxy did not come up")
            if proc.poll() is not None:
                raise RuntimeError(
                    f"proxy process exited early (rc={proc.returncode}); "
                    "port may be held by a stale instance")
            rtts, ips, locs = [], [], []
            for rnd in range(args.rounds):
                ok1, rtt, ip, loc, err = dial_once(
                    node, socks_port, args.target204, args.target_trace,
                    args.timeout)
                if not ok1:
                    raise RuntimeError(f"round {rnd+1}: {err}")
                rtts.append(rtt); ips.append(ip); locs.append(loc)
                if rnd < args.rounds - 1:
                    time.sleep(random.uniform(args.interval_min, args.interval_max))
            if max(rtts) >= RTT_LIMIT_MS:
                raise RuntimeError(f"rtt {max(rtts):.0f}ms >= {RTT_LIMIT_MS}ms")
            res.update(ok=True, rtt_ms=round(max(rtts), 1),
                       exit_ip=ips[-1], loc=locs[-1])
        finally:
            proc.terminate()
            try: proc.wait(timeout=5)
            except subprocess.TimeoutExpired: proc.kill()
            shutil.rmtree(tmp, ignore_errors=True)
    except Exception as e:
        res["err"] = str(e)[:300]
    finally:
        if relay:
            relay.close()
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("uris", nargs="*", help="node URIs")
    ap.add_argument("-f", "--file", help="file with one URI per line")
    ap.add_argument("--port", type=int, default=10808)
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--timeout", type=int, default=15)
    ap.add_argument("--interval-min", type=float, default=30)
    ap.add_argument("--interval-max", type=float, default=45)
    ap.add_argument("--target204", default=DEF_204)
    ap.add_argument("--target-trace", default=DEF_TRACE)
    ap.add_argument("--via-connect-proxy", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    uris = list(args.uris)
    if args.file:
        with open(args.file) as f:
            uris += [l.strip() for l in f if l.strip() and not l.startswith("#")]
    results = []
    port = args.port
    for u in uris:
        r = test_node(u, args, port)
        results.append(r)
        port += 1  # avoid TIME_WAIT clashes
        print(json.dumps(r, ensure_ascii=False), flush=True)
    out = json.dumps(results, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w") as f:
            f.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
