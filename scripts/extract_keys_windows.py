#!/usr/bin/env python3
"""Extract Weixin 4.x SQLCipher keys on Windows with Frida.

The Windows client statically links OpenSSL, so PKCS5_PBKDF2_HMAC is not an
export.  We locate SQLCipher's provider table setup in Weixin.dll and hook its
`kdf` callback instead.  The callback ABI is stable in Tencent SQLCipher and
gives us the salt, work factor and derived key without scanning arbitrary
process memory.
"""

import glob
import json
import os
import pathlib
import re
import struct
import subprocess
import sys
import threading
import time


_STATE_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "wechat-insight"
)
KEYS_FILE = os.path.abspath(os.environ.get("WECHAT_INSIGHT_KEYS_PATH", os.path.join(_STATE_DIR, "wechat-keys.json")))
CONFIG_FILE = os.path.abspath(os.environ.get("WECHAT_INSIGHT_CONFIG_PATH", os.path.join(_STATE_DIR, "wechat-insight.json")))
FRIDA_LOG = os.path.join(os.environ.get("TEMP", os.path.expanduser("~")), "wechat_frida_keys.log")


def find_weixin_exe():
    candidates = [
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Tencent", "Weixin", "Weixin.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Tencent", "Weixin", "Weixin.exe"),
    ]
    return next((p for p in candidates if p and os.path.isfile(p)), None)


def find_weixin_dll(exe_path=None):
    exe_path = exe_path or find_weixin_exe()
    if not exe_path:
        return None
    root = os.path.dirname(exe_path)
    dlls = glob.glob(os.path.join(root, "*", "Weixin.dll"))
    if not dlls:
        return None
    return max(dlls, key=lambda p: (os.path.getmtime(p), p))


def find_wechat_base():
    docs = pathlib.Path.home() / "Documents"
    candidates = [
        docs / "xwechat_files",
        pathlib.Path(os.environ.get("USERPROFILE", str(pathlib.Path.home()))) / "Documents" / "xwechat_files",
    ]
    return str(next((p for p in candidates if p.is_dir()), candidates[0]))


def _rip_target(image_base, instruction_rva, displacement):
    return image_base + instruction_rva + 7 + displacement


def resolve_sqlcipher_kdf_rva(dll_path):
    """Find sqlcipher_provider.kdf using the provider-name function as an anchor.

    SQLCipher's OpenSSL provider setup writes function pointers to a struct.
    `get_provider_name` is a tiny function returning the unique ``openssl``
    string.  Once its table assignment (offset 0x10) is found, the assignment
    to offset 0x30 is the KDF callback.
    """
    try:
        import pefile
    except ImportError as exc:
        raise RuntimeError("缺少 pefile，请运行: python -m pip install pefile") from exc

    pe = pefile.PE(dll_path, fast_load=True)
    image_base = pe.OPTIONAL_HEADER.ImageBase
    raw = pathlib.Path(dll_path).read_bytes()
    exec_sections = [s for s in pe.sections if s.Characteristics & 0x20000000]

    string_vas = []
    for match in re.finditer(b"openssl\x00", raw):
        try:
            string_vas.append(image_base + pe.get_rva_from_offset(match.start()))
        except Exception:
            continue

    provider_name_vas = []
    for section in exec_sections:
        blob = raw[section.PointerToRawData:section.PointerToRawData + section.SizeOfRawData]
        section_va = image_base + section.VirtualAddress
        for i in range(len(blob) - 8):
            # lea rax,[rip+disp32]; ret
            if blob[i:i + 3] != b"\x48\x8d\x05" or blob[i + 7] != 0xC3:
                continue
            disp = struct.unpack_from("<i", blob, i + 3)[0]
            if section_va + i + 7 + disp in string_vas:
                provider_name_vas.append(section_va + i)

    for section in exec_sections:
        blob = raw[section.PointerToRawData:section.PointerToRawData + section.SizeOfRawData]
        section_va = image_base + section.VirtualAddress
        for i in range(len(blob) - 7):
            if blob[i:i + 3] != b"\x48\x8d\x05":
                continue
            disp = struct.unpack_from("<i", blob, i + 3)[0]
            if section_va + i + 7 + disp not in provider_name_vas:
                continue
            # This LEA must be followed by mov [rcx+0x10],rax.
            if blob[i + 7:i + 11] != b"\x48\x89\x41\x10":
                continue
            window_start = max(0, i - 96)
            window_end = min(len(blob), i + 192)
            window = blob[window_start:window_end]
            # Find `lea rax,[rip+disp32]; mov [rcx+0x30],rax`.
            marker = b"\x48\x89\x41\x30"
            pos = window.find(marker)
            while pos >= 7:
                lea = pos - 7
                if window[lea:lea + 3] == b"\x48\x8d\x05":
                    insn_rva = section.VirtualAddress + window_start + lea
                    kdf_disp = struct.unpack_from("<i", window, lea + 3)[0]
                    return _rip_target(image_base, insn_rva, kdf_disp) - image_base
                pos = window.find(marker, pos + 1)

    raise RuntimeError(f"无法在 {dll_path} 中定位 SQLCipher OpenSSL KDF；可能需要更新特征")


def build_frida_script(kdf_rva):
    return r"""
'use strict';
const KDF_RVA = %d;
let installed = false;

function toHex(value, length) {
    if (!value || length <= 0) return '';
    const p = ptr(value);
    if (p.isNull()) return '';
    const bytes = new Uint8Array(p.readByteArray(Math.min(length, 64)));
    let out = '';
    for (let i = 0; i < bytes.length; i++) out += ('0' + bytes[i].toString(16)).slice(-2);
    return out;
}

function install(module) {
    if (installed || module.name.toLowerCase() !== 'weixin.dll') return;
    installed = true;
    const address = module.base.add(KDF_RVA);
    send({type: 'status', msg: 'Hooked sqlcipher_openssl_kdf at ' + address, pid: Process.id});
    Interceptor.attach(address, {
        onEnter(args) {
            this.algorithm = args[1].toInt32();
            this.password = args[2];
            this.passwordLen = args[3].toInt32();
            this.salt = args[4];
            this.saltLen = args[5].toInt32();
            this.rounds = args[6].toInt32();
            this.derivedKeyLen = args[7].toInt32();
            this.derivedKey = args[8];
        },
        onLeave(retval) {
            try {
                send({type: 'key', data: {
                    symbol: 'sqlcipher_openssl_kdf',
                    algorithm: this.algorithm,
                    rounds: this.rounds,
                    salt: toHex(this.salt, this.saltLen),
                    dk: toHex(this.derivedKey, this.derivedKeyLen),
                    dkLen: this.derivedKeyLen,
                    saltLen: this.saltLen,
                    passwordLen: this.passwordLen,
                    status: retval.toInt32()
                }});
            } catch (e) {
                send({type: 'error', msg: String(e)});
            }
        }
    });
}

const current = Process.findModuleByName('Weixin.dll');
if (current) install(current);
Process.attachModuleObserver({onAdded(module) { install(module); }});
""" % kdf_rva


def wanted_db_paths(db_base):
    paths = []
    for p in sorted(glob.glob(os.path.join(db_base, "message", "message_*.db"))):
        if re.match(r"^message_\d+\.db$", os.path.basename(p)):
            paths.append(p)
    for sub, name in (("contact", "contact.db"), ("session", "session.db")):
        path = os.path.join(db_base, sub, name)
        if os.path.isfile(path):
            paths.append(path)
    return paths


def pick_db_base(wechat_base):
    dirs = glob.glob(os.path.join(wechat_base, "*", "db_storage"))
    if not dirs:
        return None
    def active(path):
        candidate = os.path.join(path, "message", "message_0.db")
        return os.path.getmtime(candidate if os.path.exists(candidate) else path)
    return max(dirs, key=active)


def verify_and_match(db_path, entries):
    salt = pathlib.Path(db_path).read_bytes()[:16].hex()
    for entry in entries:
        if entry.get("rounds") == 256000 and entry.get("salt") == salt and len(entry.get("dk", "")) >= 64:
            return entry["dk"][:64]
    return None


def capture_keys(timeout=180, restart=True):
    try:
        import frida
    except ImportError as exc:
        raise RuntimeError("缺少 frida，请运行: python -m pip install frida frida-tools") from exc

    exe = find_weixin_exe()
    dll = find_weixin_dll(exe)
    if not exe or not dll:
        raise RuntimeError("未找到 Windows 微信 4.x（Weixin.exe / Weixin.dll）")
    kdf_rva = resolve_sqlcipher_kdf_rva(dll)
    print(f"  Weixin.dll: {dll}")
    print(f"  SQLCipher KDF RVA: 0x{kdf_rva:x}")

    if restart:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-Process Weixin -ErrorAction SilentlyContinue | Stop-Process -Force"],
            check=False, capture_output=True,
        )
        time.sleep(2)

    device = frida.get_local_device()
    script_source = build_frida_script(kdf_rva)
    attached = set()
    sessions = []
    entries = []
    lock = threading.Lock()
    stop = threading.Event()

    def on_message(message, _data):
        if message.get("type") != "send":
            print(f"  [FRIDA] {message}")
            return
        payload = message.get("payload", {})
        if payload.get("type") == "status":
            print(f"  {payload.get('msg')}")
        elif payload.get("type") == "error":
            print(f"  [ERROR] {payload.get('msg')}")
        elif payload.get("type") == "key":
            entry = payload["data"]
            if entry.get("status") != 0 or entry.get("rounds") != 256000:
                return
            identity = (entry.get("salt"), entry.get("dk"))
            with lock:
                if identity not in {(x.get("salt"), x.get("dk")) for x in entries}:
                    entries.append(entry)
                    with open(FRIDA_LOG, "a", encoding="utf-8") as fh:
                        fh.write(json.dumps(entry) + "\n")
                    print(f"  [KEY] salt={entry.get('salt','')[:16]}... dk={entry.get('dk','')[:16]}...")

    def attach_new_processes():
        while not stop.is_set():
            try:
                processes = device.enumerate_processes()
            except Exception:
                time.sleep(0.05)
                continue
            for process in processes:
                if process.name.lower() != "weixin.exe" or process.pid in attached:
                    continue
                attached.add(process.pid)
                try:
                    session = device.attach(process.pid)
                    script = session.create_script(script_source)
                    script.on("message", on_message)
                    script.load()
                    sessions.append((session, script))
                    print(f"  已附加 Weixin.exe PID {process.pid}")
                except Exception as exc:
                    print(f"  PID {process.pid} 附加失败: {exc}")
            stop.wait(0.02)

    if os.path.exists(FRIDA_LOG):
        os.remove(FRIDA_LOG)
    worker = threading.Thread(target=attach_new_processes, daemon=True)
    worker.start()
    if restart:
        subprocess.Popen([exe], cwd=os.path.dirname(exe))

    print(f"  正在捕获密钥，最长等待 {timeout} 秒。请保持微信登录。")
    deadline = time.time() + timeout
    try:
        while time.time() < deadline:
            time.sleep(1)
            db_base = pick_db_base(find_wechat_base())
            if db_base and entries:
                paths = wanted_db_paths(db_base)
                if paths and all(verify_and_match(p, entries) for p in paths):
                    break
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        worker.join(1)
        for session, _script in sessions:
            try:
                session.detach()
            except Exception:
                pass
    return entries


def save_configuration(entries):
    wechat_base = find_wechat_base()
    db_base = pick_db_base(wechat_base)
    if not db_base:
        raise RuntimeError(f"未找到数据库目录: {wechat_base}")
    result = {}
    for path in wanted_db_paths(db_base):
        key = verify_and_match(path, entries)
        if key:
            result[os.path.splitext(os.path.basename(path))[0]] = key
    if not result:
        raise RuntimeError("捕获到了 KDF 调用，但没有密钥能与数据库 salt 匹配")

    os.makedirs(os.path.dirname(KEYS_FILE), exist_ok=True)
    with open(KEYS_FILE, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)
    wxid = pathlib.Path(db_base).parent.name
    config = {}
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, encoding="utf-8") as fh:
            config = json.load(fh)
    config.update({
        "wxid": wxid,
        "db_base_path": db_base,
        "data_dir": config.get("data_dir", os.path.join(_STATE_DIR, "data")),
        "report_dir": config.get("report_dir", os.path.join(_STATE_DIR, "reports")),
    })
    os.makedirs(config["data_dir"], exist_ok=True)
    os.makedirs(config["report_dir"], exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as fh:
        json.dump(config, fh, indent=2, ensure_ascii=False)
    print(f"  已匹配 {len(result)} 个数据库密钥")
    print(f"  密钥文件: {KEYS_FILE}")
    print(f"  配置文件: {CONFIG_FILE}")
    return result


def main(argv=None):
    if sys.platform != "win32":
        raise RuntimeError("此入口仅支持 Windows")
    entries = capture_keys()
    if not entries:
        print("  未捕获密钥。请确认微信已正常启动并登录，然后重试。")
        return 1
    save_configuration(entries)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
