import time
import threading
import queue
import yaml
import serial
import argparse
import os
import sys
import datetime
from ctypes import *

# ================= 0. 配置刷写 Payload 字典 (保留原始数据) =================
CONFIG_PAYLOADS = {
    "PHL (ECO)":[0xF1, 0x08, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0x02, 0x06, 0x01, 0x11,
                0x01, 0x00, 0x00, 0x00, 0x00, 0x11, 0x01, 0x11, 0x11, 0x00, 0x01, 0x00, 0x00, 0x00, 0x10, 0x11,
                0x11, 0x00, 0x30, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x31],
    "PHL (PLUS)": [0xF1, 0x08, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0x02, 0x07, 0x11, 0x11,
                0x11, 0x10, 0x01, 0x10, 0x01, 0x11, 0x01, 0x11, 0x11, 0x00, 0x10, 0x11, 0x10, 0x00, 0x10, 0x11,
                0x11, 0x10, 0x10, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x76],
    "VN (ECO)": [0xF1, 0x08, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0x01, 0x06, 0x01, 0x11,
                0x01, 0x00, 0x00, 0x00, 0x00, 0x11, 0x01, 0x11, 0x11, 0x00, 0x01, 0x00, 0x00, 0x00, 0x10, 0x11,
                0x11, 0x00, 0x30, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x34],
    "VN (PLUS)": [0xF1, 0x08, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0x01, 0x07, 0x01, 0x11,
                 0x11, 0x10, 0x01, 0x10, 0x01, 0x11, 0x01, 0x11, 0x11, 0x00, 0x10, 0x11, 0x10, 0x00, 0x10, 0x11,
                 0x11, 0x10, 0x10, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xF4],
    "VN (ULTRA)": [0xF1, 0x08, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0x01, 0x09, 0x11, 0x11,
                  0x11, 0x10, 0x01, 0x10, 0x01, 0x11, 0x01, 0x11, 0x11, 0x00, 0x10, 0x11, 0x10, 0x00, 0x10, 0x11,
                  0x11, 0x10, 0x10, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xB0],
    "INDO (ECO)": [0xF1, 0x08, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0x0D, 0x06, 0x02, 0x11,
                     0x11, 0x10, 0x01, 0x10, 0x01, 0x11, 0x01, 0x11, 0x11, 0x00, 0x11, 0x11, 0x00, 0x00, 0x10, 0x11,
                     0x11, 0x00, 0x30, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x6B],
    "INDO (PLUS)": [0xF1, 0x08, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0x0D, 0x07, 0x12, 0x11,
                      0x11, 0x10, 0x01, 0x10, 0x01, 0x11, 0x01, 0x11, 0x11, 0x00, 0x10, 0x11, 0x10, 0x00, 0x10, 0x11,
                      0x11, 0x00, 0x10, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xD1],
    "INDIA (BASE)": [0xF1, 0x08, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0x11, 0x08, 0x02, 0x11,
                       0x01, 0x10, 0x01, 0x10, 0x01, 0x11, 0x01, 0x11, 0x11, 0x00, 0x11, 0x11, 0x00, 0x00, 0x10, 0x11,
                       0x11, 0x00, 0x30, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x21],
    "INDIA (MIDA1)": [0xF1, 0x08, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0x11, 0x0A, 0x02, 0x11,
                        0x11, 0x10, 0x01, 0x10, 0x01, 0x11, 0x01, 0x11, 0x11, 0x00, 0x11, 0x11, 0x00, 0x00, 0x10, 0x11,
                        0x11, 0x00, 0x30, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xA1],
    "INDIA (MIDB1)": [0xF1, 0x08, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0x11, 0x0B, 0x02, 0x11,
                        0x11, 0x10, 0x01, 0x10, 0x01, 0x11, 0x01, 0x11, 0x11, 0x00, 0x10, 0x11, 0x10, 0x00, 0x10, 0x11,
                        0x11, 0x00, 0x10, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x9C],
    "INDIA (TOP)": [0xF1, 0x08, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0x11, 0x0C, 0x12, 0x11,
                      0x11, 0x10, 0x01, 0x10, 0x01, 0x11, 0x01, 0x11, 0x11, 0x00, 0x10, 0x11, 0x10, 0x00, 0x10, 0x11,
                      0x11, 0x00, 0x10, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xF4]
}

# ================= 0.5 算法库加载 =================
generate_key_func = None
project_name = "VF6.PY"

try:
    sec_lib_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib", "libVF6.so")
    if os.path.exists(sec_lib_path):
        sec_lib = cdll.LoadLibrary(sec_lib_path)
        func_name = "ZLGKey" if hasattr(sec_lib, "ZLGKey") else "getKey"
        if hasattr(sec_lib, func_name):
            generate_key_func = getattr(sec_lib, func_name)
            generate_key_func.argtypes = [POINTER(c_ubyte), c_ushort, c_uint, c_char_p, POINTER(c_ubyte),
                                          POINTER(c_ushort)]
            generate_key_func.restype = c_int
            print(f"[系统] 成功挂载安全算法函数: {func_name}")
except Exception as e:
    print(f"[异常] 加载算法库失败: {e}")

# ================= 1. Linux 下 ZLG CANFD 库定义 (严格参考 DEMO) =================
try:
    lib = cdll.LoadLibrary("./libusbcanfd.so")
except OSError:
    lib = cdll.LoadLibrary("libusbcanfd.so")

USBCANFD = c_uint32(33)
CMD_CAN_TTX = 0x16  # 设置定时发送列表
CMD_CAN_TTX_CTL = 0x17  # 定时发送开关
CMD_CAN_TRES = 0x18  # 终端电阻


class ZCAN_MSG_INFO(Structure):
    _fields_ = [("txm", c_uint, 4), ("fmt", c_uint, 4), ("sdf", c_uint, 1),
                ("sef", c_uint, 1), ("err", c_uint, 1), ("brs", c_uint, 1),
                ("est", c_uint, 1), ("tx", c_uint, 1), ("echo", c_uint, 1),
                ("qsend_100us", c_uint, 1), ("qsend", c_uint, 1), ("pad", c_uint, 15)]


class ZCAN_MSG_HDR(Structure):
    _fields_ = [("ts", c_uint32), ("id", c_uint32), ("inf", ZCAN_MSG_INFO),
                ("pad", c_uint16), ("chn", c_uint8), ("len", c_uint8)]


class ZCAN_20_MSG(Structure):
    _fields_ = [("hdr", ZCAN_MSG_HDR), ("dat", c_ubyte * 8)]


class ZCAN_FD_MSG(Structure):
    _fields_ = [("hdr", ZCAN_MSG_HDR), ("dat", c_ubyte * 64)]


# 定时发送专用结构体
class ZCAN_TTX(Structure):
    _fields_ = [("interval", c_uint32), ("repeat", c_uint16), ("index", c_uint8),
                ("flags", c_uint8), ("msg", ZCAN_FD_MSG)]


class ZCAN_TTX_CFG(Structure):
    _fields_ = [("size", c_uint32), ("table", ZCAN_TTX * 8)]


class abit_config(Structure):
    _fields_ = [("tseg1", c_uint8), ("tseg2", c_uint8), ("sjw", c_uint8), ("smp", c_uint8), ("brp", c_uint16)]


class dbit_config(Structure):
    _fields_ = [("tseg1", c_uint8), ("tseg2", c_uint8), ("sjw", c_uint8), ("smp", c_uint8), ("brp", c_uint16)]


class ZCANFD_INIT(Structure):
    _fields_ = [("clk", c_uint32), ("mode", c_uint32), ("abit", abit_config), ("dbit", dbit_config)]


# ================= 2. 日志捕获模块 =================
class DualLogger:
    def __init__(self, filename):
        self.terminal = sys.stdout
        self.log = open(filename, "a", encoding="utf-8")

    def write(self, message):
        self.terminal.write(message);
        self.log.write(message);
        self.log.flush()

    def flush(self):
        self.terminal.flush();
        self.log.flush()


# ================= 3. 电阻箱控制 =================
class QR10Controller:
    def __init__(self, port="/dev/ttyUSB0", baudrate=115200):
        try:
            self.ser = serial.Serial(port, baudrate, timeout=1)
        except Exception as e:
            print(f"连接电阻箱失败: {e}")
            raise

    def set_resistance(self, value):
        if str(value).lower() == 'infinity':
            val_to_send = 1200000
        else:
            val_to_send = value

        cmd = f"AT+USER.SP={val_to_send}\r\n".encode()
        self.ser.write(cmd)
        time.sleep(1)
        response = self.ser.read_all().decode().strip()
        print(f"[硬件操作] 切换目标阻值: {value} Ω | 设备响应: {response}")

        query_cmd = b"AT+USER.GP\r\n"
        self.ser.write(query_cmd)
        time.sleep(0.1)
        query_response = self.ser.read_all().decode().strip()
        if query_response:
            print(f"{query_response}")

    def close(self):
        if hasattr(self, 'ser') and self.ser.is_open:
            self.ser.close()


# ================= 4. CAN与诊断通信层 =================
class CANDiagnostic:
    def __init__(self, target_port=0):
        self.dev_type = USBCANFD
        self.dev_idx = c_uint32(0)
        self.target_port = target_port
        self.thread_flag = False
        self.uds_queue = queue.Queue()
        self.latest_signals = {0x381: [0] * 8, 0x488: [0] * 8}
        self.UDS_REQ_ID = 0x688
        self.UDS_RESP_ID = 0x608

    def setup_background_messages(self):
        """完全按照 DEMO 逻辑实现硬件定时发送"""
        print("\n[前置条件] 正在将 DBC 周期报文注入底层自动发送队列...")

        messages = [
            {"id": 0x20D, "data": [0x0A, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00], "ms": 20},
            {"id": 0x112, "data": [0x2D, 0x00, 0x00, 0x00, 0x02, 0x00, 0x00, 0x00], "ms": 100},
            {"id": 0x40D, "data": [0x17, 0x00, 0x00, 0x00, 0x00, 0x07, 0xD0, 0x00], "ms": 120},
        ]

        cfg = ZCAN_TTX_CFG()
        cfg.size = sizeof(ZCAN_TTX) * len(messages)

        for i, m in enumerate(messages):
            cfg.table[i].interval = m["ms"] * 10  # 100us单位
            cfg.table[i].repeat = 0  # 循环
            cfg.table[i].index = i
            cfg.table[i].flags = 1  # 使能

            msg = cfg.table[i].msg
            msg.hdr.id = m["id"]
            msg.hdr.len = 8
            msg.hdr.inf.txm = 0
            msg.hdr.inf.fmt = 0  # CAN2.0
            for j in range(8): msg.dat[j] = m["data"][j]

        # 指令 0x16 注入
        if lib.VCI_SetReference(self.dev_type, self.dev_idx, self.target_port, CMD_CAN_TTX, byref(cfg)) == 0:
            print(" [!] 警告: 周期报文注入失败")
            return

        # 指令 0x17 开启
        on = c_uint(1)
        if lib.VCI_SetReference(self.dev_type, self.dev_idx, self.target_port, CMD_CAN_TTX_CTL, byref(on)) == 1:
            print(" ✅ 硬件后台定时报文发送已开启。")

    def connect(self):
        if lib.VCI_OpenDevice(self.dev_type, self.dev_idx, 0) == 0: raise Exception("打开CAN失败")

        canfd_init = ZCANFD_INIT()
        canfd_init.clk = 60000000;
        canfd_init.mode = 0
        canfd_init.abit.tseg1 = 14;
        canfd_init.abit.tseg2 = 3;
        canfd_init.abit.sjw = 2;
        canfd_init.abit.brp = 5
        canfd_init.dbit.tseg1 = 10;
        canfd_init.dbit.tseg2 = 2;
        canfd_init.dbit.sjw = 2;
        canfd_init.dbit.brp = 1

        lib.VCI_InitCAN(self.dev_type, self.dev_idx, self.target_port, byref(canfd_init))
        lib.VCI_SetReference(self.dev_type, self.dev_idx, self.target_port, CMD_CAN_TRES, byref(c_uint8(1)))

        # 初始启动背景报文
        self.setup_background_messages()

        lib.VCI_StartCAN(self.dev_type, self.dev_idx, self.target_port)
        self.thread_flag = True
        self.rx_thread = threading.Thread(target=self._receive_thread)
        self.rx_thread.daemon = True;
        self.rx_thread.start()

    def _calculate_key(self, seed_bytes, sub_func):
        """按照图中判断逻辑：01->L1, 03->L2, 05->L3, 07->L4"""
        if not generate_key_func: return None
        try:
            level_map = {0x01: 1, 0x03: 2, 0x05: 3, 0x07: 4}
            target_level = level_map.get(sub_func, 1)

            seed_array = (c_ubyte * len(seed_bytes))(*seed_bytes)
            key_array = (c_ubyte * 32)();
            key_len = c_ushort(32)
            variant = b"VF6.PY"

            ret = generate_key_func(seed_array, c_ushort(len(seed_bytes)), c_uint(target_level), variant, key_array,
                                    byref(key_len))
            if ret == 0: return [key_array[i] for i in range(key_len.value)]
        except Exception as e:
            print(f"计算失败: {e}")
        return None

    # --- 核心诊断逻辑 (维持 ISO-TP) ---
    def _receive_thread(self):
        while self.thread_flag:
            count = lib.VCI_GetReceiveNum(self.dev_type, self.dev_idx, self.target_port)
            if count > 0:
                can_data = (ZCAN_20_MSG * count)()
                rcount = lib.VCI_Receive(self.dev_type, self.dev_idx, self.target_port, byref(can_data), count, 50)
                for i in range(rcount):
                    can_id = can_data[i].hdr.id & 0x1FFFFFFF
                    data = [can_data[i].dat[j] for j in range(can_data[i].hdr.len)]
                    self._process_rx_msg(can_id, data)
            time.sleep(0.005)

    def _process_rx_msg(self, can_id, data):
        """核心处理 RX 消息，确保所有 0x608 报文（含流控）均打印"""
        if can_id == self.UDS_RESP_ID:
            print(f"[UDS RX] ID: 0x{can_id:03X}, "
                  f"Data: {[f'0x{x:02x}' for x in data]}")
            self.uds_queue.put(data)
        elif can_id in [0x381, 0x488]:
            self.latest_signals[can_id] = data

    def _send_uds_isotp(self, payload):
        if len(payload) <= 7:
            self._send_raw([len(payload)] + payload)
        else:
            self._send_raw([0x10 | (len(payload) >> 8), len(payload) & 0xFF] + payload[:6])
            try:
                resp = self.uds_queue.get(timeout=1.0)
                if (resp[0] >> 4) != 0x3: return
            except:
                return
            offset, seq = 6, 1
            while offset < len(payload):
                self._send_raw([0x20 | seq] + payload[offset:offset + 7])
                offset += 7;
                seq = (seq + 1) % 16;
                time.sleep(0.01)

    def _send_raw(self, payload):
        msg = (ZCAN_20_MSG * 1)()
        msg[0].hdr.id = self.UDS_REQ_ID;
        msg[0].hdr.chn = self.target_port;
        msg[0].hdr.len = 8
        for i in range(8): msg[0].dat[i] = payload[i] if i < len(payload) else 0x00
        print(f"[UDS TX] ID: 0x{self.UDS_REQ_ID:03X}, "
                f"Data: {[f'0x{x:02x}' for x in payload]}")
        lib.VCI_Transmit(self.dev_type, self.dev_idx, self.target_port, byref(msg), 1)

    def _recv_isotp(self, timeout=2.0):
        try:
            f = self.uds_queue.get(timeout=timeout)
            if (f[0] >> 4) == 0x0: return f[1:1 + (f[0] & 0x0F)]
            if (f[0] >> 4) == 0x1:
                t_len = ((f[0] & 0x0F) << 8) | f[1];
                res = list(f[2:]);
                self._send_raw([0x30, 0, 0, 0, 0, 0, 0, 0])
                while len(res) < t_len:
                    cf = self.uds_queue.get(timeout=1.0)
                    if (cf[0] >> 4) == 0x2: res.extend(cf[1:])
                return res[:t_len]
        except:
            return []

    def write_variant_config(self, variant_name, payload):
        print(f"\n[配置刷写] === 自动识别并刷写版本配置: {variant_name} ===")

        # 1. 10 03
        print(f"\n[配置刷写] 1. 进入扩展会话 (10 03)")
        self._send_uds_isotp([0x10, 0x03])
        self._recv_isotp(timeout=1.0)
        time.sleep(0.3)

        # 2. 85 02
        print(f"\n[配置刷写] 2. 关闭DTC设置 (85 02)")
        self._send_uds_isotp([0x85, 0x02])
        self._recv_isotp(timeout=1.0)
        time.sleep(0.3)

        # 3. 10 41
        print(f"\n[配置刷写] 3. 进入特定会话 (10 41)")
        self._send_uds_isotp([0x10, 0x41])
        self._recv_isotp(timeout=1.0)
        time.sleep(0.3)

        # 4. 安全访问 (27 05)
        print("[配置刷写] 4. 安全访问解锁 (27 05)")
        sub_req = 0x05
        self.uds_queue.queue.clear()
        self._send_uds_isotp([0x27, sub_req])
        seed_resp = self._recv_isotp(1.0)
        if seed_resp and seed_resp[0] == 0x67:
            key = self._calculate_key(seed_resp[2:], sub_req)
            if key:
                self._send_uds_isotp([0x27, sub_req + 1] + key)
                if self._recv_isotp(1.0): print(" ✅ 安全解锁成功")

        # 5. 2E F1 08
        print(f"\n[配置刷写] 5. 写入配置数据 (2E F1 08 ... 共{len(payload)}字节)")
        self._send_uds_isotp([0x2E] + payload)
        self._recv_isotp(timeout=1.0)
        time.sleep(0.3)

        # 6. 22 F1 08
        print(f"\n[配置刷写] 6. 读取验证配置数据 (22 F1 08)")
        self._send_uds_isotp([0x22, 0xF1, 0x08])
        self._recv_isotp(timeout=1.0)
        time.sleep(0.3)

        # 7. 10 03
        print(f"\n[配置刷写] 7. 准备重置，进入扩展会话 (10 03)")
        self._send_uds_isotp([0x10, 0x03])
        self._recv_isotp(timeout=1.0)
        time.sleep(0.3)

        # 8. 硬件重置 (11 01)
        print(f"\n[配置刷写] 8. 硬件重置 (11 01)")
        self._send_uds_isotp([0x11, 0x01])
        self._recv_isotp(timeout=1.0)

        print(f"\n[配置刷写] 等待 ECU 重启应用生效 (5s)...")
        self.setup_background_messages()
        time.sleep(5.0)

    def read_dtc(self):
        self._send_uds_isotp([0x19, 0x02, 0xFF])

        full_resp = self._recv_isotp(1.0)
        if not full_resp or full_resp[0] != 0x59 or full_resp[1] != 0x02:
            return []

        dtc_data = full_resp[2:]
        dtc_list = []
        for i in range(0, len(dtc_data), 4):
            if i + 3 >= len(dtc_data): break
            status = dtc_data[i]
            high = dtc_data[i + 1]
            mid = dtc_data[i + 2]
            low = dtc_data[i + 3]
            dtc_list.append(f"{high:02X}{mid:02X}{low:02X}-{status:02X}")

        return dtc_list

    def clear_dtc(self):
        self._send_uds_isotp([0x14, 0xFF, 0xFF, 0xFF]);
        self._recv_isotp(0.5)

    def get_seat_status(self, channel_name):
        data = self.latest_signals.get(0x381, [0] * 8)
        signal_configs = {
            "INDIA (TOP)_Seat_Occupancy_2nd_Row_Dri": {"byte": 2, "shift": 2},
            "INDIA (TOP)_Seat_Occupancy_2nd_Row_Pas": {"byte": 2, "shift": 4},
            "INDIA (TOP)_Seat_Occupancy_2nd_Row_Mid": {"byte": 2, "shift": 6},
            "INDO (ECO)_Seat_Occupancy_Passenger": {"byte": 2, "shift": 0},
            "INDO (ECO)_Seat_Occupancy_2nd_Row_Dri": {"byte": 2, "shift": 2},
            "INDO (ECO)_Seat_Occupancy_2nd_Row_Pas": {"byte": 2, "shift": 4},
            "INDO (ECO)_Seat_Occupancy_2nd_Row_Mid": {"byte": 2, "shift": 6},
            "INDO (PLUS)_Seat_Occupancy_2nd_Row_Dri": {"byte": 2, "shift": 2},
            "INDO (PLUS)_Seat_Occupancy_2nd_Row_Pas": {"byte": 2, "shift": 4},
            "INDO (PLUS)_Seat_Occupancy_2nd_Row_Mid": {"byte": 2, "shift": 6},
            "VN (ECO)_Seat_Occupancy_Passenger": {"byte": 2, "shift": 0},
            "PHL (ECO)_Seat_Occupancy_Passenger": {"byte": 2, "shift": 0},
        }
        config = signal_configs.get(channel_name)
        if not config: return "Unknown"
        val = (data[config["byte"]] >> config["shift"]) & 0x03
        mapping = {0x0: "Seat_Not_Occupied", 0x1: "Seat Occupied", 0x2: "Not_Available", 0x3: "Failure"}
        return mapping.get(val, "Unknown")

    def get_acu_status(self):
        data = self.latest_signals.get(0x488, [0] * 8)
        val = data[2] & 0x01
        mapping = {0x0: "ACU_Failure", 0x1: "ACU_Normal_Operation"}
        return mapping.get(val, "Unknown")

    def close(self):
        self.thread_flag = False
        if hasattr(self, 'rx_thread'): self.rx_thread.join(1.0)
        try:
            lib.VCI_ResetCAN(self.dev_type, self.dev_idx, self.target_port)
            lib.VCI_CloseDevice(self.dev_type, self.dev_idx)
        except Exception:
            pass

# ================= 5. 自动化测试引擎 =================
def run_test_suite(yaml_path, can_device, qr10_device):
    try:
        with open(yaml_path, 'r', encoding='utf-8') as f:
            test_suite = yaml.safe_load(f)
    except Exception as e:
        print(f"读取YAML文件失败 ({yaml_path}): {e}")
        return

    channel_name = test_suite.get('channel_name', 'Unknown')
    print(f"开始执行测试套件: {channel_name} | 文件: {yaml_path}")
    print("==================================================")

    # =============== 前置条件准备 =================
    if test_suite.get('cases') and len(test_suite['cases']) > 0:
        initial_res = test_suite['cases'][0]['resistance']
        print(f"\n[环境准备] 正在建立硬件前置条件，设置初始阻值: {initial_res} Ω")
        qr10_device.set_resistance(initial_res)
        print(f"\n[环境准备] 等待 ECU 故障消除与稳定 (5s)...")
        time.sleep(5.0)

    # =============== 自动识别并刷写特定版本 =================
    target_variant = None
    upper_path = yaml_path.upper() + " " + channel_name.upper()

    if "INDO" in upper_path and "ECO" in upper_path:
        target_variant = "INDO (ECO)"
    elif "INDO" in upper_path and "PLUS" in upper_path:
        target_variant = "INDO (PLUS)"
    elif "INDIA" in upper_path and "TOP" in upper_path:
        target_variant = "INDIA (TOP)"
    elif "PHL" in upper_path and "ECO" in upper_path:
        target_variant = "PHL (ECO)"
    elif "VN" in upper_path and "ECO" in upper_path:
        target_variant = "VN (ECO)"

    if target_variant and target_variant in CONFIG_PAYLOADS:
        payload = CONFIG_PAYLOADS[target_variant]
        can_device.write_variant_config(target_variant, payload)

    print(" 开始前清除故障码...")
    for i in range(3):
        can_device.clear_dtc()
        time.sleep(1.0)

    passed_cases = 0
    total_cases = len(test_suite.get('cases', []))

    for case in test_suite.get('cases', []):
        print(f"\n▶ 正在执行用例: {case['id']} - {case['description']}")

        qr10_device.set_resistance(case['resistance'])
        print(" 等待 ECU 故障确诊 (8s)...")
        time.sleep(8.0)

        actual_dtc_first = can_device.read_dtc()
        time.sleep(1.0)

        actual_dtc = can_device.read_dtc()
        time.sleep(1.0)

        actual_seat_status = can_device.get_seat_status(channel_name)
        actual_acu_status = can_device.get_acu_status()
        expected = case['expected']

        seat_pass = (actual_seat_status == expected['seat_status'])
        acu_pass = (actual_acu_status == expected['acu_status'])
        dtc_pass = (set(actual_dtc) == set(expected['dtc']))

        case_passed = seat_pass and acu_pass and dtc_pass

        print(
            f"[Seat Status] 预期: {expected['seat_status']:<18} | 实际: {actual_seat_status:<18} -> {'PASS' if seat_pass else 'FAIL'}")
        print(
            f"[ACU  Status] 预期: {expected['acu_status']:<18} | 实际: {actual_acu_status:<18} -> {'PASS' if acu_pass else 'FAIL'}")
        print(
            f"[DTC   Check] 预期: {str(expected['dtc']):<18} | 实际: {str(actual_dtc):<18} -> {'PASS' if dtc_pass else 'FAIL'}")

        if case_passed:
            passed_cases += 1
            print(f" 用例 {case['id']} 测试通过!")
        else:
            print(f" 用例 {case['id']} 测试失败!")

        print(" 清除故障码...")
        for i in range(3):
            can_device.clear_dtc()
            time.sleep(1.0)

    print("\n" + "=" * 50)
    print(f" 测试套件执行完毕! 总计: {total_cases} | 通过: {passed_cases} | 失败: {total_cases - passed_cases}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="座椅/ACU 自动化硬件测试执行脚本")
    parser.add_argument("-y", "--yaml", type=str, required=True, help="要执行的 YAML 测试用例文件路径")
    parser.add_argument("-p", "--port", type=str, default="/dev/ttyUSB0", help="QR10电阻箱串口名称")
    parser.add_argument("-b", "--baudrate", type=int, default=115200, help="串口波特率")

    args = parser.parse_args()

    channel_name = "Unknown"
    if os.path.exists(args.yaml):
        try:
            with open(args.yaml, 'r', encoding='utf-8') as f:
                ts = yaml.safe_load(f)
                channel_name = ts.get('channel_name', 'Unknown')
        except Exception:
            pass

    log_dir = "ODS_log"
    os.makedirs(log_dir, exist_ok=True)
    current_time = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_filename = f"{channel_name}_log_{current_time}.log"
    log_filepath = os.path.join(log_dir, log_filename)

    logger = DualLogger(log_filepath)
    sys.stdout = logger
    sys.stderr = logger

    print(f"=== 日志记录启动 | 文件: {log_filepath} ===")

    can = CANDiagnostic()
    qr10 = None
    try:
        qr10 = QR10Controller(port=args.port, baudrate=args.baudrate)
        can.connect()
        time.sleep(1.0)
        run_test_suite(args.yaml, can, qr10)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"\n系统异常: {e}")
    finally:
        if qr10: qr10.close()
        can.close()