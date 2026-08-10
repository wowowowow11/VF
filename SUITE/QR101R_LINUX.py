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

# ================= 0. 配置刷写 Payload 字典 =================
CONFIG_PAYLOADS = {
    "INDO (BASE)": [0xF1, 0x08, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x35, 0x90, 0x24, 0x00, 0x49,
                    0x00, 0x00, 0x08, 0x09, 0x08, 0x00, 0x02, 0x10, 0x12, 0x02, 0x00, 0x00, 0x88, 0x90, 0x10, 0x00,
                    0x00, 0x00, 0x00, 0x00, 0x03, 0x00, 0x00, 0x00, 0x00, 0x00, 0xA0],
    "INDIA (BASE)": [0xF1, 0x08, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x45, 0x90, 0x24, 0x00, 0x49,
                     0x00, 0x09, 0x08, 0x09, 0x48, 0x04, 0x02, 0x10, 0x12, 0x02, 0x00, 0x00, 0x88, 0x90, 0x10, 0x00,
                     0x00, 0x00, 0x00, 0x00, 0x03, 0x00, 0x00, 0x00, 0x00, 0x00, 0x8D],
    "VN (BASE)": [0xF1, 0x08, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x15, 0x50, 0x24, 0x00, 0x40, 0x00,
                  0x00, 0x08, 0x09, 0x08, 0x00, 0x02, 0x10, 0x00, 0x00, 0x00, 0x00, 0x88, 0x90, 0x10, 0x00, 0x00, 0x00,
                  0x00, 0x00, 0x03, 0x00, 0x00, 0x00, 0x00, 0x00, 0xBF],
    "INDO (FULL)": [0xF1, 0x08, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x35, 0x90, 0x24, 0x41, 0x49,
                    0x00, 0x09, 0x08, 0x09, 0x48, 0x04, 0x02, 0x10, 0x12, 0x02, 0x01, 0x00, 0x88, 0x90, 0x10, 0x00,
                    0x00, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x06],
    "INDIA (FULL)": [0xF1, 0x08, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x45, 0x90, 0x24, 0x41, 0x49,
                     0x00, 0x09, 0x08, 0x09, 0x48, 0x04, 0x02, 0x01, 0x12, 0x02, 0x00, 0x00, 0x88, 0x90, 0x10, 0x00,
                     0x00, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x4A],
    "VN (FULL)": [0xF1, 0x08, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x15, 0x50, 0x24, 0x41, 0x49, 0x00,
                  0x09, 0x08, 0x09, 0x48, 0x04, 0x02, 0x00, 0x12, 0x02, 0x00, 0x00, 0x88, 0x90, 0x10, 0x24, 0x00, 0x00,
                  0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x05]
}

# ================= 0.5 自动静默加载算法库 libVF6.so =================
generate_key_func = None
project_name = "VF6.PY"

try:
    sec_lib_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib", "libVF6.so")
    if os.path.exists(sec_lib_path):
        sec_lib = cdll.LoadLibrary(sec_lib_path)
        func_name = "ZLGKey" if hasattr(sec_lib, "ZLGKey") else "getKey"
        if hasattr(sec_lib, func_name):
            generate_key_func = getattr(sec_lib, func_name)
            generate_key_func.argtypes = [
                POINTER(c_ubyte),  # iSeedArray
                c_ushort,          # iSeedArraySize
                c_uint,            # iSecurityLevel
                c_char_p,          # iVariant
                POINTER(c_ubyte),  # iKeyArray
                POINTER(c_ushort)  # iKeyArraySize
            ]
            generate_key_func.restype = c_int
            print(f"[系统] 成功挂载安全算法函数: {func_name}")
        else:
            print("[警告] 算法库中未找到 ZLGKey 或 getKey 函数")
except Exception as e:
    print(f"[系统] 加载算法库失败: {e}")

# ================= 1. Linux 下 ZLG CANFD 库加载与结构体定义 =================
try:
    lib = cdll.LoadLibrary("libusbcanfd.so")
except OSError:
    try:
        lib = cdll.LoadLibrary("./libusbcanfd.so")
    except Exception as e:
        print(f"无法加载 libusbcanfd.so: {e}")
        sys.exit(1)

USBCANFD = c_uint32(33)
CMD_CAN_TRES = 0x18

class ZCAN_AUTO_CAN_OBJ(Structure):
    _fields_ = [("can_id", c_uint32), ("can_dlc", c_uint8), ("pad", c_uint8 * 3), ("data", c_uint8 * 8)]


class ZCAN_AUTO_TRANSMIT_OBJ(Structure):
    _fields_ = [("enable", c_uint16), ("index", c_uint16), ("interval", c_uint32), ("obj", ZCAN_AUTO_CAN_OBJ)]


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


class abit_config(Structure):
    _fields_ = [("tseg1", c_uint8), ("tseg2", c_uint8), ("sjw", c_uint8),
                ("smp", c_uint8), ("brp", c_uint16)]


class dbit_config(Structure):
    _fields_ = [("tseg1", c_uint8), ("tseg2", c_uint8), ("sjw", c_uint8),
                ("smp", c_uint8), ("brp", c_uint16)]


class ZCANFD_INIT(Structure):
    _fields_ = [("clk", c_uint32), ("mode", c_uint32),
                ("abit", abit_config), ("dbit", dbit_config)]

# ================= 2. 日志捕获模块 =================
class DualLogger:
    def __init__(self, filename):
        self.terminal = sys.stdout
        self.log = open(filename, "a", encoding="utf-8")

    def write(self, message):
        self.terminal.write(message)
        self.log.write(message)
        self.log.flush()

    def flush(self):
        self.terminal.flush()
        self.log.flush()

# ================= 3. 硬件控制层 =================
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

    # --- 新增周期性背景报文注入函数 ---
    def setup_background_messages(self):
        """利用硬件底层使能背景循环报文"""
        print("\n[前置条件] 正在将 DBC 周期报文注入底层自动发送队列...")

        # 0. 先清除之前的设置
        clear_path = f"{self.target_port}/clear_auto_send".encode("utf-8")
        lib.VCI_SetReference(self.dev_type, self.dev_idx, self.target_port, 0x21, clear_path)

        messages = [
            {"id": 0x200, "data": [0x0A, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00], "interval_ms": 20},
            {"id": 0x112, "data": [0x2D, 0x00, 0x00, 0x00, 0x02, 0x00, 0x00, 0x00], "interval_ms": 100},
            {"id": 0x40D, "data": [0x17, 0x00, 0x00, 0x00, 0x0B, 0x07, 0xD0, 0x00], "interval_ms": 120}
        ]

        for i, msg in enumerate(messages):
            auto_can = ZCAN_AUTO_TRANSMIT_OBJ()
            memset(addressof(auto_can), 0, sizeof(auto_can))
            auto_can.index = i
            auto_can.enable = 1
            auto_can.interval = msg["interval_ms"]
            auto_can.obj.can_id = msg["id"]
            auto_can.obj.can_dlc = len(msg["data"])
            for j in range(len(msg["data"])):
                auto_can.obj.data[j] = msg["data"][j]

            val_path = f"{self.target_port}/auto_send".encode("utf-8")
            lib.VCI_SetReference(self.dev_type, self.dev_idx, self.target_port, 0x20, byref(auto_can))

        # 应用设置
        apply_path = f"{self.target_port}/apply_auto_send".encode("utf-8")
        lib.VCI_SetReference(self.dev_type, self.dev_idx, self.target_port, 0x22, apply_path)
        print(" [v] 硬件后台定时报文发送已开启。")

    def connect(self):

        ret = lib.VCI_OpenDevice(self.dev_type, self.dev_idx, 0)
        if ret == 0: raise Exception("打开 CAN 设备失败！")

        canfd_init = ZCANFD_INIT()
        canfd_init.clk = 60000000
        canfd_init.mode = 0
        canfd_init.abit.tseg1 = 14;
        canfd_init.abit.tseg2 = 3;
        canfd_init.abit.sjw = 2;
        canfd_init.abit.smp = 0;
        canfd_init.abit.brp = 5
        canfd_init.dbit.tseg1 = 10;
        canfd_init.dbit.tseg2 = 2;
        canfd_init.dbit.sjw = 2;
        canfd_init.dbit.smp = 0;
        canfd_init.dbit.brp = 1

        ret = lib.VCI_InitCAN(self.dev_type, self.dev_idx, self.target_port, byref(canfd_init))
        if ret == 0: raise Exception(f"初始化通道 Port {self.target_port + 1} 失败！")

        res_val = c_uint8(1)
        lib.VCI_SetReference(self.dev_type, self.dev_idx, self.target_port, CMD_CAN_TRES, byref(res_val))

        ret = lib.VCI_StartCAN(self.dev_type, self.dev_idx, self.target_port)
        if ret == 0: raise Exception(f"启动通道 Port {self.target_port + 1} 失败！")

        self.setup_background_messages()  # <--- 注入周期报文

        self.thread_flag = True
        self.rx_thread = threading.Thread(target=self._receive_thread)
        self.rx_thread.daemon = True
        self.rx_thread.start()

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
        if can_id == self.UDS_RESP_ID:
            self.uds_queue.put(data)
            print(f"[UDS RX] ID: 0x{can_id:03X}, Data: {[hex(x) for x in data]}")
        elif can_id in [0x381, 0x488]:
            self.latest_signals[can_id] = data

    def _send_uds_request(self, payload):
        msg = (ZCAN_20_MSG * 1)()
        msg[0].hdr.inf.txm = 0;
        msg[0].hdr.inf.fmt = 0;
        msg[0].hdr.inf.sdf = 0;
        msg[0].hdr.inf.sef = 0
        msg[0].hdr.id = self.UDS_REQ_ID
        msg[0].hdr.chn = self.target_port
        msg[0].hdr.len = 8

        for i in range(8):
            msg[0].dat[i] = payload[i] if i < len(payload) else 0x00

        print(f"[UDS TX] ID: 0x{self.UDS_REQ_ID:03X}, Data: {[hex(x) for x in payload]}")
        lib.VCI_Transmit(self.dev_type, self.dev_idx, self.target_port, byref(msg), 1)

    def clear_uds_queue(self):
        while not self.uds_queue.empty():
            self.uds_queue.get()

    def _send_uds_request_isotp(self, payload):
        if len(payload) <= 7:
            data = [len(payload)] + payload
            self._send_uds_request(data)
        else:
            data = [0x10 | (len(payload) >> 8), len(payload) & 0xFF] + payload[:6]
            self._send_uds_request(data)

            fc_received = False
            try:
                start_time = time.time()
                while time.time() - start_time < 2.0:
                    resp = self.uds_queue.get(timeout=0.5)
                    if (resp[0] >> 4) == 0x3:
                        fc_received = True
                        break
            except queue.Empty:
                pass

            if not fc_received: return

            seq = 1
            offset = 6
            while offset < len(payload):
                chunk = payload[offset:offset + 7]
                data = [0x20 | seq] + chunk
                self._send_uds_request(data)
                seq = (seq + 1) % 16
                offset += 7
                time.sleep(0.01)

    def _recv_uds_response_isotp(self, timeout=2.0):
        try:
            first_resp = self.uds_queue.get(timeout=timeout)
        except queue.Empty:
            return []

        pci = first_resp[0] >> 4
        if pci == 0x0:
            sf_len = first_resp[0] & 0x0F
            return first_resp[1:1 + sf_len]
        elif pci == 0x1:
            total_len = ((first_resp[0] & 0x0F) << 8) | first_resp[1]
            data = list(first_resp[2:])
            self._send_uds_request([0x30, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
            next_seq = 1
            while len(data) < total_len:
                try:
                    cf = self.uds_queue.get(timeout=0.5)
                    if (cf[0] >> 4) == 0x2 and (cf[0] & 0x0F) == next_seq:
                        data.extend(cf[1:])
                        next_seq = (next_seq + 1) % 16
                except queue.Empty:
                    break
            return data[:total_len]
        return []

    def _calculate_key(self, seed_bytes, security_level):
        """
        参考 ReadCrashData.py 中的 call_zlgkey 方法实现
        """
        if not generate_key_func:
            print("[错误] 安全算法函数未挂载")
            return None
        try:
            # 1. 转换输入参数 (ctypes 类型转换)
            seed_array_c = (c_ubyte * len(seed_bytes))(*seed_bytes)
            seed_len_c = c_ushort(len(seed_bytes))
            security_level_c = c_uint(3)
            variant_c = c_char_p(b"VF6.PY") # 对应 project_name

            # 2. 准备输出
            key_array_c = (c_ubyte * 16)() # 预留空间
            key_len_c = c_ushort(16)

            # 3. 调用
            ret = generate_key_func(
                seed_array_c,
                seed_len_c,
                security_level_c,
                variant_c,
                key_array_c,
                byref(key_len_c)
            )

            if ret == 0 and key_len_c.value > 0:
                key = [key_array_c[i] for i in range(key_len_c.value)]
                print(f"[算法] Key计算成功: {[hex(x) for x in key]}")
                return key
            else:
                print(f"[错误] 算法返回码: {ret}")
                return None
        except Exception as e:
            print(f"[异常] 算法执行失败: {e}")
            return None

    def clear_uds_queue(self):
        while not self.uds_queue.empty(): self.uds_queue.get()

    def write_variant_config(self, variant_name, payload):
        """
        完整的刷写流程，集成安全解锁逻辑
        """
        print(f"\n[配置刷写] 目标版本: {variant_name}")

    def write_variant_config(self, variant_name, payload):
        print(f"\n[配置刷写] === 自动识别并刷写版本配置: {variant_name} ===")

        # 1. 10 03
        print("[配置刷写] 1. 进入扩展会话 (10 03)")
        self.clear_uds_queue()
        self._send_uds_request_isotp([0x10, 0x03])
        self._recv_uds_response_isotp(timeout=1.0)
        time.sleep(0.3)

        # 2. 85 02
        print("[配置刷写] 2. 关闭DTC设置 (85 02)")
        self.clear_uds_queue()
        self._send_uds_request_isotp([0x85, 0x02])
        self._recv_uds_response_isotp(timeout=1.0)
        time.sleep(0.3)

        # 3. 10 41
        print("[配置刷写] 3. 进入特定会话 (10 41)")
        self.clear_uds_queue()
        self._send_uds_request_isotp([0x10, 0x41])
        self._recv_uds_response_isotp(timeout=1.0)
        time.sleep(0.3)

        # 4. 27 05 & 27 06
        print("[配置刷写] 4. 请求安全访问解锁 (27 05 & 27 06)")
        self.clear_uds_queue()
        self._send_uds_request_isotp([0x27, 0x05])
        seed_resp = self._recv_uds_response_isotp(1.0)

        if seed_resp and len(seed_resp) > 2 and seed_resp[0] == 0x67 and seed_resp[1] == 0x05:
            seed = seed_resp[2:]
            # 参考 ReadCrash 的 level 传参 (通常请求 05 传入 level 为 5)
            key = self._calculate_key(seed, security_level=5)

            if key:
                print(" -> 发送密钥 (27 06)")
                self.clear_uds_queue()
                self._send_uds_request_isotp([0x27, 0x06] + key)
                res = self._recv_uds_response_isotp(1.0)
                if res and res[1] == 0x06:
                    print(" ✅ 解锁成功")
                else:
                    print(" ❌ 解锁被拒绝")
            else:
                print(" ❌ Key 计算失败")
        else:
            print(" ❌ 种子获取失败")

        # 5. 2E F1 08
        print(f"[配置刷写] 5. 写入配置数据 (2E F1 08 ... 共{len(payload)}字节)")
        self.clear_uds_queue()
        self._send_uds_request_isotp([0x2E] + payload)
        self._recv_uds_response_isotp(timeout=1.0)
        time.sleep(0.3)

        # 6. 22 F1 08
        print("[配置刷写] 6. 读取验证配置数据 (22 F1 08)")
        self.clear_uds_queue()
        self._send_uds_request_isotp([0x22, 0xF1, 0x08])
        self._recv_uds_response_isotp(timeout=1.0)
        time.sleep(0.3)

        # 7. 10 03
        print("[配置刷写] 7. 准备重置，进入扩展会话 (10 03)")
        self.clear_uds_queue()
        self._send_uds_request_isotp([0x10, 0x03])
        self._recv_uds_response_isotp(timeout=1.0)
        time.sleep(0.3)

        # 8. 硬件重置 (11 01)
        print("[配置刷写] 8. 硬件重置 (11 01)")
        self.clear_uds_queue()
        self._send_uds_request_isotp([0x11, 0x01])
        self._recv_uds_response_isotp(timeout=1.0)

        print("[配置刷写] 等待 ECU 重启应用生效 (5s)...")
        time.sleep(5.0)

        # --- 重启后重新确保周期报文在发，防止后续 clear_dtc 报错 22 ---
        self.setup_background_messages()

    def read_dtc(self):
        self.clear_uds_queue()
        self._send_uds_request_isotp([0x19, 0x02, 0xFF])

        full_resp = self._recv_uds_response_isotp(1.0)
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
        # 1. 强制进入扩展会话，赋予清除DTC的权限
        self.clear_uds_queue()
        self._send_uds_request_isotp([0x10, 0x01])
        self._recv_uds_response_isotp(timeout=0.5)
        time.sleep(0.1)

        # 2. 清除故障码
        self.clear_uds_queue()
        self._send_uds_request_isotp([0x14, 0xFF, 0xFF, 0xFF])
        self._recv_uds_response_isotp(timeout=0.5)
        time.sleep(0.5)

    def get_seat_status(self, channel_name):
        data = self.latest_signals.get(0x381, [0] * 8)
        signal_configs = {
            "INDIA (FULL)_Seat_Occupancy_Passenger": {"byte": 2, "shift": 0},
            "INDIA (FULL)_Seat_Occupancy_2nd_Row_Dri": {"byte": 2, "shift": 2},
            "INDIA (FULL)_Seat_Occupancy_2nd_Row_Pas": {"byte": 2, "shift": 4},
            "INDIA (FULL)_Seat_Occupancy_2nd_Row_Mid": {"byte": 2, "shift": 6},
            "INDIA (BASE)_Seat_Occupancy_Passenger": {"byte": 2, "shift": 0},
            "INDIA (BASE)_Seat_Occupancy_2nd_Row_Dri": {"byte": 2, "shift": 2},
            "INDIA (BASE)_Seat_Occupancy_2nd_Row_Pas": {"byte": 2, "shift": 4},
            "INDIA (BASE)_Seat_Occupancy_2nd_Row_Mid": {"byte": 2, "shift": 6},
            "INDO (FULL)_Seat_Occupancy_Passenger": {"byte": 2, "shift": 0},
            "INDO (FULL)_Seat_Occupancy_2nd_Row_Dri": {"byte": 2, "shift": 2},
            "INDO (FULL)_Seat_Occupancy_2nd_Row_Pas": {"byte": 2, "shift": 4},
            "INDO (FULL)_Seat_Occupancy_2nd_Row_Mid": {"byte": 2, "shift": 6},
            "INDO (BASE)_Seat_Occupancy_Passenger": {"byte": 2, "shift": 0},
            "INDO (BASE)_Seat_Occupancy_2nd_Row_Dri": {"byte": 2, "shift": 2},
            "INDO (BASE)_Seat_Occupancy_2nd_Row_Pas": {"byte": 2, "shift": 4},
            "INDO (BASE)_Seat_Occupancy_2nd_Row_Mid": {"byte": 2, "shift": 6},
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
        print(f" [环境准备] 正在建立硬件前置条件，设置初始阻值: {initial_res} Ω")
        qr10_device.set_resistance(initial_res)
        print(" [环境准备] 等待 ECU 故障消除与稳定 (5s)...")
        time.sleep(5.0)

    # =============== 自动识别并刷写特定版本 =================
    target_variant = None
    upper_path = yaml_path.upper() + " " + channel_name.upper()

    if "VN" in upper_path and "FULL" in upper_path:
        target_variant = "VN (FULL)"
    elif "VN" in upper_path and "BASE" in upper_path:
        target_variant = "VN (BASE)"
    elif "INDO" in upper_path and "FULL" in upper_path:
        target_variant = "INDO (FULL)"
    elif "INDO" in upper_path and "BASE" in upper_path:
        target_variant = "INDO (BASE)"
    elif "INDIA" in upper_path and "FULL" in upper_path:
        target_variant = "INDIA (FULL)"
    elif "INDIA" in upper_path and "BASE" in upper_path:
        target_variant = "INDIA (BASE)"

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
            f"  [ACU  Status] 预期: {expected['acu_status']:<18} | 实际: {actual_acu_status:<18} -> {'PASS' if acu_pass else 'FAIL'}")
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


# ================= 6. 命令行执行入口 =================
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