#!/usr/bin/env python3
"""小火箭订阅打包：每方案一个 txt（每行一条连接串）
→ 每方案一个 Base64 订阅文件 + 一个二维码 PNG。

二维码内容默认为订阅 URL 本身（--qr-mode url）：Shadowrocket 当前版本
扫 sub:// 形式会报 "Invalid config URL"；--qr-mode sub 可回退旧格式。

用法示例：
  python3 sub_pack.py --in links/ --out dist/ --url-base https://example.com/subs
  # links/ 下有 serv00.txt / railway.txt / proton.txt（每行一条连接串）
  # dist/ 下生成 serv00-sub.txt / serv00-qr.png ……
  # 订阅文件托管到 --url-base 对应的地址后，二维码即生效

不做任何网络拨测，纯本地打包。
"""
import argparse
import base64
import os
import subprocess
import sys


def ensure_qrcode():
    try:
        import qrcode
        return qrcode
    except ImportError:
        print("[*] 缺 qrcode 库，正在 pip --user 安装…", file=sys.stderr)
        env = dict(os.environ)
        env.setdefault("TMPDIR", os.path.expanduser("~/workspace/pip-tmp"))
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--user", "-q", "qrcode", "pillow"],
            env=env,
        )
        import qrcode
        return qrcode


def build_sub_payload(uris):
    """连接串换行拼接 → Base64（小火箭标准订阅内容）。"""
    return base64.b64encode("\n".join(uris).encode("utf-8")).decode("utf-8")


def build_sub_qr_text(sub_url, remark, mode="url"):
    """二维码内容：url=订阅 URL 本身（现行小火箭要求）；sub=旧 sub:// 形式。"""
    if mode == "sub":
        b64 = base64.urlsafe_b64encode(sub_url.encode("utf-8")).decode("utf-8").rstrip("=")
        return f"sub://{b64}#{remark}"
    return sub_url


def main():
    ap = argparse.ArgumentParser(description="小火箭订阅打包：txt → Base64订阅 + 订阅URL二维码")
    ap.add_argument("--in", dest="indir", required=True, help="输入目录，每方案一个 <方案名>.txt")
    ap.add_argument("--out", dest="outdir", required=True, help="输出目录")
    ap.add_argument("--url-base", default="https://SUB_HOST",
                    help="订阅文件托管基地址（占位符 https://SUB_HOST，运行时传入真实地址）")
    ap.add_argument("--qr-mode", choices=["url", "sub"], default="url",
                    help="二维码内容：url=纯订阅URL（默认，现行小火箭要求）；sub=旧 sub:// 形式")
    args = ap.parse_args()

    qrcode = ensure_qrcode()
    os.makedirs(args.outdir, exist_ok=True)

    names = sorted(f[:-4] for f in os.listdir(args.indir) if f.endswith(".txt"))
    if not names:
        sys.exit("输入目录里没有 .txt 文件")

    for name in names:
        with open(os.path.join(args.indir, name + ".txt"), encoding="utf-8") as f:
            uris = [ln.strip() for ln in f if ln.strip() and not ln.strip().startswith("#")]
        if not uris:
            print(f"[!] {name}.txt 为空，跳过")
            continue

        b64 = build_sub_payload(uris)
        sub_file = os.path.join(args.outdir, f"{name}-sub.txt")
        with open(sub_file, "w", encoding="utf-8") as f:
            f.write(b64 + "\n")

        sub_url = f"{args.url_base.rstrip('/')}/{name}-sub.txt"
        qr_text = build_sub_qr_text(sub_url, name, args.qr_mode)
        qr = qrcode.QRCode(box_size=10, border=2)
        qr.add_data(qr_text)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        if min(img.size) < 300:  # 保证 300x300 以上
            k = (300 + min(img.size) - 1) // min(img.size)
            img = img.resize((img.size[0] * k, img.size[1] * k))
        png = os.path.join(args.outdir, f"{name}-qr.png")
        img.save(png)
        print(f"[OK] {name}: {sub_file}（{len(uris)} 条）+ {png}（{img.size[0]}x{img.size[1]}）")


if __name__ == "__main__":
    main()
