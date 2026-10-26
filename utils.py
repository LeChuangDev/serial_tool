# ====================== 纯函数工具 ======================

def parse_hex(text):
    """解析 HEX 字符串为字节。

    返回 (bytes, err_msg)：成功时 err_msg 为 None；
    失败时 data 为 None，err_msg 定位到具体出错元素。
    """
    t = (text.replace("0x", "").replace("0X", "").replace(",", " ")
         .replace("，", " ").replace("\n", " ").replace("\r", " "))
    parts = t.split()
    if not parts:
        return b"", None
    for i, x in enumerate(parts):
        try:
            v = int(x, 16)
        except ValueError:
            return None, f"第 {i + 1} 个元素 '{x}' 不是合法十六进制"
        if v > 0xFF:
            return None, f"第 {i + 1} 个元素 '{x}' 超出单字节范围 (00-FF)"
    return bytes(int(x, 16) for x in parts), None


def crc16_modbus(data):
    """Modbus CRC16 校验值。"""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def crc8(data, poly=0x07):
    """CRC-8 校验值（多项式 0x07，初值 0x00，无反射）。"""
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ poly) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def xor_sum(data):
    """逐字节异或校验值（单字节）。"""
    v = 0
    for b in data:
        v ^= b
    return v


def byte_sum(data):
    """累加和校验值（单字节，取低 8 位）。"""
    return sum(data) & 0xFF


def hex_str(data):
    """字节转大写十六进制字符串（空格分隔）。"""
    return " ".join(f"{i:02X}" for i in data)
