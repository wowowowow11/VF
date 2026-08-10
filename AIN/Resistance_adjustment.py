""" ****************************************************************************
**    Author:  Waythink Test Engineering Team                                 **
**    Date:    2026.07.01                                                     **
**    Version: 2.5.3.0 (Added automatic retry up to 3 times on NRC/Timeout)   **
**    Project: VF6NP - Standalone Auto AIN Resistance Sweep Test              **
**************************************************************************** """

import os
import sys

# 【核心修复 1】：强制切换执行上下文至当前脚本所在目录
current_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(current_dir)

import time
import ctypes
import datetime
import csv
import platform
import serial
import threading
import queue
from zlgcan import *  # 引入官方包装库

# ==================== 硬件及连接配置 (请根据实际情况调整) ====================
# CAN卡配置
DEVICE_TYPE = ZCAN_USBCANFD_200U  # 设备类型
DEVICE_INDEX = 0                  # 设备索引号
CHANNEL_INDEX = 0                 # 通道索引号 (通道0 / Port 1)
CAN_BAUDRATE = 500000             # CAN 仲裁域/标准波特率 500kbps

# UDS 诊断 ID 配置
UDS_REQ_ID = 0x688                # 物理寻址请求 ID
UDS_RESP_ID = 0x608               # ECU 响应 ID

# 电阻箱配置
RESISTOR_PORT = "COM10"           # 电阻箱串口号

# 安全访问配置
dll_path = os.path.join(current_dir, "LIMO7ZGLDll.dll")

# 全局变量控制后台接收与 0x85 报文共享
thread_flag = True
uds_queue = queue.Queue()

# 新增 0x85 报文共享区与保护锁
msg_85_lock = threading.Lock()
latest_msg_85 = None

# 在线保持（Tester Present）软件使能开关
tester_present_enabled = False

# ==================== 全局日志输出与多线程写锁 ====================
global_log_file = None  # 全局日志路径
log_lock = threading.Lock()  # 保护多线程同时调用写入时的日志顺序

def log_and_print(msg):
    """同时向控制台输出并追加写入当前的专属 .log 文件中，包含毫秒级时间戳"""
    global global_log_file
    with log_lock:
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        # 支持多行文本拆分，使电阻箱返回的多行信息每行都对齐时间戳
        lines = str(msg).split('\n')
        for line in lines:
            full_msg = f"[{timestamp}] {line}"
            print(full_msg)
            if global_log_file:
                try:
                    with open(global_log_file, "a", encoding="utf-8") as lf:
                        lf.write(full_msg + "\n")
                except:
                    pass
# =============================================================================


# ==================== 安全访问算法加载 (适配 Python 3.8+) ====================
try:
    if sys.version_info >= (3, 8):
        os.add_dll_directory(current_dir)
    security_dll = ctypes.CDLL(dll_path)
    print(f" 安全算法库加载成功: {dll_path}")
except Exception as e:
    security_dll = None
    print(f"[WARNING] 无法载入安全算法库 '{dll_path}': {e}")


def call_zlgkey(seed_array, security_level, variant):
    if security_dll is None:
        raise RuntimeError("安全动态库未就绪")
    seed_array_c = (ctypes.c_ubyte * len(seed_array))(*seed_array)
    seed_array_size = ctypes.c_ushort(len(seed_array))
    security_level_c = ctypes.c_uint(security_level)
    variant_c = ctypes.c_char_p(variant.encode('utf-8'))
    key_array = (ctypes.c_ubyte * 16)()
    key_array_size = ctypes.c_ushort(16)

    security_dll.ZLGKey.argtypes = [
        ctypes.POINTER(ctypes.c_ubyte), ctypes.c_ushort,
        ctypes.c_uint, ctypes.c_char_p,
        ctypes.POINTER(ctypes.c_ubyte), ctypes.POINTER(ctypes.c_ushort)
    ]
    security_dll.ZLGKey.restype = ctypes.c_int

    result = security_dll.ZLGKey(
        seed_array_c, seed_array_size, security_level_c,
        variant_c, key_array, ctypes.byref(key_array_size)
    )
    if result != 0:
        raise RuntimeError(f"算法库密钥计算失败，错误码: {result}")
    return list(key_array[:key_array_size.value])


# ==================== QR10 电阻箱控制类 ====================
class QR10Controller:
    def __init__(self, port, baudrate=115200):
        try:
            self.ser = serial.Serial(port, baudrate, timeout=1)
        except Exception as e:
            print(f"[ERROR] 串口打开失败 ({port}): {e}")
            raise

    def set_resistance(self, value):
        val_to_send = 1200000 if str(value).lower() == 'infinity' else value

        # 1. 核心修复：在发送新指令前，彻底清空串口接收缓冲区
        if hasattr(self, 'ser') and self.ser.is_open:
            self.ser.reset_input_buffer()

        # 2. 发送当前设定指令
        cmd = f"AT+USER.SP={val_to_send}\r\n".encode()
        self.ser.write(cmd)

        # 3. 稍微增加等待时间（0.15s - 0.2s），给继电器物理切换及数据回复留出充裕时间
        time.sleep(0.18)

        # 4. 读取当前指令的真实响应
        response = self.ser.read_all().decode().strip()
        log_and_print(f"[硬件控制] 电阻箱设定: {value} Ω | 设备响应: {response}")

    def close(self):
        if hasattr(self, 'ser') and self.ser.is_open:
            self.ser.close()


# ==================== ZLG CAN 后台接收线程 ====================
def receive_thread_func(zcan, chn_handle):
    global thread_flag, latest_msg_85
    while thread_flag:
        rcv_num = zcan.GetReceiveNum(chn_handle, ZCAN_TYPE_CAN)
        if rcv_num:
            read_cnt = min(rcv_num, 100)
            rcv_msg, actual_num = zcan.Receive(chn_handle, read_cnt, 50)
            for i in range(actual_num):
                frame = rcv_msg[i].frame
                can_id = frame.can_id & 0x1FFFFFFF
                dlc = frame.can_dlc
                data = [frame.data[j] for j in range(dlc)]

                # 仅在后台捕获并存储 UDS 响应帧，避免被其他背景噪声帧覆盖
                if can_id == UDS_RESP_ID:
                    uds_queue.put(data)

                # 捕获 ACU 发送的 0x85 周期/反馈报文
                elif can_id == 0x85:
                    with msg_85_lock:
                        latest_msg_85 = data
                    # 【核心功能】：实时、全量打印总线上收到的每一个 0x85 原始报文并记录到日志中
                    hex_85 = " ".join([f"{b:02X}" for b in data])
                    log_and_print(f"[0x085 实时接收] DATA: {hex_85}")
        time.sleep(0.005)


# ==================== Tester Present 软件静默后台线程 ====================
def tester_present_thread_func(zcan, chn_handle):
    global thread_flag, tester_present_enabled
    while thread_flag:
        if tester_present_enabled:
            # 静默发送 Tester Present 维持报文，直接调用底层 Transmit 不记录日志
            tx_obj = ZCAN_Transmit_Data()
            tx_obj.frame.can_id = UDS_REQ_ID
            tx_obj.frame.can_dlc = 8
            tx_obj.transmit_type = 0  # 正常发送
            tp_payload = [0x02, 0x3E, 0x80, 0x00, 0x00, 0x00, 0x00, 0x00]
            for i in range(8):
                tx_obj.frame.data[i] = tp_payload[i]
            zcan.Transmit(chn_handle, tx_obj, 1)
        time.sleep(2.0)  # 每 2s 发送一次


# ==================== 对齐 cdscoding 稳定款 UDS-ISO-TP 收发逻辑 ====================
def send_uds_raw(zcan, chn_handle, req_id, payload_8bytes):
    tx_obj = ZCAN_Transmit_Data()
    tx_obj.frame.can_id = req_id
    tx_obj.frame.can_dlc = 8
    tx_obj.transmit_type = 0  # 正常发送
    for i in range(8):
        tx_obj.frame.data[i] = payload_8bytes[i]

    # 打印并将底层发送报文写入日志 (TX Log)
    tx_hex = " ".join([f"{b:02X}" for b in payload_8bytes])
    log_and_print(f"[TX] ID: 0x{req_id:03X} | DATA: {tx_hex}")
    return zcan.Transmit(chn_handle, tx_obj, 1) == 1


def wait_uds_response(timeout=2.0):
    try:
        data = uds_queue.get(timeout=timeout)
        # 打印并将底层接收报文写入日志 (RX Log)
        rx_hex = " ".join([f"{b:02X}" for b in data])
        log_and_print(f"[RX] ID: 0x{UDS_RESP_ID:03X} | DATA: {rx_hex}")
        return data
    except queue.Empty:
        return None


def parse_multi_frame_response(zcan, chn_handle, first_frame):
    if not first_frame:
        return None
    pci = first_frame[0] >> 4
    if pci != 0x1:
        return first_frame
    total_len = ((first_frame[0] & 0x0F) << 8) | first_frame[1]
    received_data = first_frame[2:]

    # 回复流控制帧 (FC)
    fc_payload = [0x30, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]
    send_uds_raw(zcan, chn_handle, UDS_REQ_ID, fc_payload)

    next_seq = 1
    start_time = time.time()
    while len(received_data) < total_len and (time.time() - start_time < 5.0):
        cont_frame = wait_uds_response(0.5)
        if not cont_frame:
            continue
        if (cont_frame[0] >> 4) == 0x2:
            received_data.extend(cont_frame[1:])
            next_seq = (next_seq + 1) % 16
    return received_data[:total_len]


def execute_service(zcan, chn_handle, service_name, payload):
    max_retries = 3
    sid = payload[0]

    for attempt in range(max_retries + 1):
        if attempt > 0:
            log_and_print(f"[RETRY] 正在对诊断服务 [{service_name}] 进行第 {attempt}/{max_retries} 次重试...")
            time.sleep(0.20)  # 稍微等待 200ms 以便于 ECU 恢复状态

        log_and_print(f"\n---> 诊断指令: {service_name} <---")

        # 清空可能存在的旧数据，防止残余帧污染
        while not uds_queue.empty():
            try:
                uds_queue.get_nowait()
            except:
                break

        # 1. 组包发送 (单帧 / 多帧)
        send_success = False
        if len(payload) <= 7:
            can_data = [len(payload)] + payload
            while len(can_data) < 8:
                can_data.append(0x00)
            if send_uds_raw(zcan, chn_handle, UDS_REQ_ID, can_data):
                send_success = True
        else:
            total_len = len(payload)
            can_data = [0x10 | (total_len >> 8), total_len & 0xFF] + payload[:6]
            if send_uds_raw(zcan, chn_handle, UDS_REQ_ID, can_data):
                fc = wait_uds_response(2.0)
                if fc and (fc[0] >> 4) == 3:
                    bs = fc[1]
                    remain_payload = payload[6:]
                    seq = 1
                    bs_count = 0
                    send_success = True
                    while remain_payload:
                        chunk = remain_payload[:7]
                        remain_payload = remain_payload[7:]
                        cf_data = [0x20 | seq] + chunk
                        while len(cf_data) < 8:
                            cf_data.append(0x00)
                        if not send_uds_raw(zcan, chn_handle, UDS_REQ_ID, cf_data):
                            send_success = False
                            break
                        seq = (seq + 1) & 0x0F
                        bs_count += 1
                        if bs != 0 and bs_count >= bs and remain_payload:
                            fc = wait_uds_response(2.0)
                            if not fc or (fc[0] >> 4) != 3:
                                send_success = False
                                break
                            bs_count = 0
                        time.sleep(0.01)
                else:
                    log_and_print("[ERROR] 未收到流控制帧(FC)")
                    send_success = False

        if not send_success:
            log_and_print("[ERROR] 数据帧传输未完成，准备重试。")
            continue  # 发送失败，触发 attempt 循环的下一次重试

        # 2. 接收响应
        success_flag = False
        result_resp = None

        while True:
            resp = wait_uds_response(3.0)
            if not resp:
                log_and_print("[ERROR] 诊断响应超时")
                break  # 跳出接收循环，触发 retry 逻辑

            pci = resp[0] >> 4
            full_resp = None

            if pci == 0x0:
                full_resp = resp[1 : 1 + (resp[0] & 0x0F)]
            elif pci == 0x1:
                full_resp = parse_multi_frame_response(zcan, chn_handle, resp)

            if full_resp:
                if full_resp[0] == 0x7F and full_resp[1] == sid:
                    if full_resp[2] == 0x78:
                        log_and_print(" 收到 NRC 78 响应挂起，继续等待...")
                        continue
                    else:
                        log_and_print(f" 收到否定响应 (NRC): 0x{full_resp[2]:02X}")
                        break  # 跳出接收循环，触发 retry 逻辑
                elif full_resp[0] == (sid + 0x40):
                    result_resp = full_resp
                    success_flag = True
                    break
                else:
                    result_resp = full_resp
                    success_flag = True
                    break

        # 如果这一轮执行成功，直接返回结果
        if success_flag:
            return result_resp

    # 如果所有重试都耗尽
    log_and_print(f"[FATAL] 诊断服务 [{service_name}] 经过 {max_retries} 次重试后依然失败。")
    return None


# ==================== DBC 定时信号发送模块 ====================
def start_hardware_auto_send(zcan, dev_handle, chn):
    # 先清空当前通道的定时列表
    zcan.ZCAN_SetValue(dev_handle, f"{chn}/clear_auto_send", "0".encode("utf-8"))

    # 1. 0x342 ABS 10ms 帧
    abs_obj = ZCAN_AUTO_TRANSMIT_OBJ()
    abs_obj.enable = 1
    abs_obj.index = 0
    abs_obj.interval = 20
    abs_obj.obj.transmit_type = 0
    abs_obj.obj.frame.can_id = 0x125
    abs_obj.obj.frame.can_dlc = 8
    abs_data = [0xD5, 0x00, 0x01, 0x18, 0x00, 0x00, 0x00, 0x00]
    for i, v in enumerate(abs_data):
        abs_obj.obj.frame.data[i] = v
    zcan.ZCAN_SetValue(dev_handle, f"{chn}/auto_send", byref(abs_obj))

    # 2. 0x112 BCM CLAMP 100ms 帧
    bcm1_obj = ZCAN_AUTO_TRANSMIT_OBJ()
    bcm1_obj.enable = 1
    bcm1_obj.index = 1
    bcm1_obj.interval = 100
    bcm1_obj.obj.transmit_type = 0
    bcm1_obj.obj.frame.can_id = 0x378
    bcm1_obj.obj.frame.can_dlc = 8
    bcm1_data = [0x02, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]
    for i, v in enumerate(bcm1_data):
        bcm1_obj.obj.frame.data[i] = v
    zcan.ZCAN_SetValue(dev_handle, f"{chn}/auto_send", byref(bcm1_obj))

    # 3. 0x10A BCM VOLTAGE 100ms 帧
    bcm2_obj = ZCAN_AUTO_TRANSMIT_OBJ()
    bcm2_obj.enable = 1
    bcm2_obj.index = 2
    bcm2_obj.interval = 100
    bcm2_obj.obj.transmit_type = 0
    bcm2_obj.obj.frame.can_id = 0x10A
    bcm2_obj.obj.frame.can_dlc = 8
    bcm2_data = [0xDC, 0x00, 0x9F, 0xD0, 0x28, 0x00, 0xC0, 0x00]
    for i, v in enumerate(bcm2_data):
        bcm2_obj.obj.frame.data[i] = v
    zcan.ZCAN_SetValue(dev_handle, f"{chn}/auto_send", byref(bcm2_obj))

    # 提交保存并启动定时发送任务
    zcan.ZCAN_SetValue(dev_handle, f"{chn}/apply_auto_send", "0".encode("utf-8"))
    print("[自动仿真] DBC 周期帧硬件定时器已开启并应用。")


def stop_hardware_auto_send(zcan, dev_handle, chn):
    zcan.ZCAN_SetValue(dev_handle, f"{chn}/clear_auto_send", "0".encode("utf-8"))
    print("[自动仿真] 周期帧定时发送已停止。")


# ==================== 主测试逻辑 ====================
def run_test():
    global seed, thread_flag, latest_msg_85, tester_present_enabled, global_log_file
    print("=" * 60)
    print("      ACU AIN 阻值自动扫频扫阻测试上位机 (Standalone)")
    print("=" * 60)

    # 1. 初始化串口电阻箱
    print(f" 正在开启电阻箱串口: {RESISTOR_PORT}...")
    try:
        resistor = QR10Controller(RESISTOR_PORT, 115200)
    except Exception:
        return

    # 2. 初始化 ZLG CAN 设备
    print(" 正在初始化 ZLG CAN 通讯卡...")
    zcan = ZCAN()
    dev_handle = zcan.OpenDevice(DEVICE_TYPE, DEVICE_INDEX, 0)
    if dev_handle == INVALID_DEVICE_HANDLE:
        print("[ERROR] 打开 CAN 卡设备失败！请确认硬件是否连接。")
        resistor.close()
        return

    zcan.ZCAN_SetValue(dev_handle, str(CHANNEL_INDEX) + "/canfd_abit_baud_rate", "500000".encode("utf-8"))
    zcan.ZCAN_SetValue(dev_handle, str(CHANNEL_INDEX) + "/initenal_resistance", "1".encode("utf-8"))

    # 开启通道配置
    chn_cfg = ZCAN_CHANNEL_INIT_CONFIG()
    chn_cfg.can_type = ZCAN_TYPE_CANFD if DEVICE_TYPE == ZCAN_USBCANFD_200U else ZCAN_TYPE_CAN
    if chn_cfg.can_type == ZCAN_TYPE_CANFD:
        chn_cfg.config.canfd.mode = 0
    else:
        chn_cfg.config.can.mode = 0
        chn_cfg.config.can.acc_code = 0
        chn_cfg.config.can.acc_mask = 0xFFFFFFFF
        chn_cfg.config.can.filter = 0
        chn_cfg.config.can.timing0 = 0x00
        chn_cfg.config.can.timing1 = 0x1C

    chn_handle = zcan.InitCAN(dev_handle, CHANNEL_INDEX, chn_cfg)
    if chn_handle == INVALID_CHANNEL_HANDLE:
        print("[ERROR] 初始化 CAN 通道失败。")
        zcan.CloseDevice(dev_handle)
        resistor.close()
        return

    if zcan.StartCAN(chn_handle) != ZCAN_STATUS_OK:
        print("[ERROR] 启动 CAN 通道失败。")
        zcan.CloseDevice(dev_handle)
        resistor.close()
        return

    print(f"CAN 通道 {CHANNEL_INDEX} 已成功启动，波特率: {CAN_BAUDRATE / 1000}kbps.")

    # 启动后台独立接收线程
    rx_thread = threading.Thread(target=receive_thread_func, args=(zcan, chn_handle))
    rx_thread.daemon = True
    rx_thread.start()

    # 启动后台静默 Tester Present 在线保持线程
    tp_thread = threading.Thread(target=tester_present_thread_func, args=(zcan, chn_handle))
    tp_thread.daemon = True
    tp_thread.start()

    try:
        # 准备数据保存目录
        data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
        os.makedirs(data_dir, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

        # 建立全局专属日志绑定
        sweep_log_file = os.path.join(data_dir, f"ain_sweep_log_{timestamp}.log")
        global_log_file = sweep_log_file

        # 3. 启动硬件周期帧仿真发送
        log_and_print(" 开始加载并执行硬件 DBC 周期帧仿真...")
        start_hardware_auto_send(zcan, dev_handle, CHANNEL_INDEX)

        # 4. 诊断解锁逻辑
        log_and_print("[1/3] 发起诊断 Extended 会话并计算安全密钥解锁...")
        # 4.1 会话切换
        resp = execute_service(zcan, chn_handle, "Diagnostic Session - Extended (10 03)", [0x10, 0x03])
        if resp and resp[0] == 0x50:
            # 只有在 Extended 成功返回后，才激活后台静默 Tester Present 线程，防止干扰会话建立
            tester_present_enabled = True
        else:
            log_and_print("[WARNING] 切换 Extended Session 响应异常或无响应。")
        time.sleep(0.1)

        # 4.2 请求种子
        resp = execute_service(zcan, chn_handle, "Security Access - Request Seed (27 01)", [0x27, 0x01])
        if resp and len(resp) >= 18:
            seed = resp[2:18]
            log_and_print("[UDS] 获取种子成功! Seed: " + " ".join(f"{b:02X}" for b in seed))
        else:
            log_and_print("[ERROR] 无法获取安全种子 Seed。")
            raise RuntimeError("Seed request failed")
        time.sleep(0.1)

        # 4.3 密匙解锁
        key = call_zlgkey(seed, 1, "default")
        resp = execute_service(zcan, chn_handle, "Security Access - Send Key (27 02)", [0x27, 0x02] + key)
        if resp and resp[0] == 0x67:
            log_and_print("[UDS] 诊断安全解锁成功！")
        else:
            log_and_print("[ERROR] 诊断安全解锁失败。")
            raise RuntimeError("Unlock failed")
        time.sleep(0.1)

        # 4.4 激活写入
        execute_service(zcan, chn_handle, "Write Data By Identifier (2E 02 33 01 55)", [0x2E, 0x02, 0x33, 0x01, 0x55])
        time.sleep(0.5)

        # 5. AIN 电阻切换与扫阻测试核心
        log_and_print("[2/3] 进入自动化 AIN 阻值递增扫阻测试 (范围 0Ω 到 100000Ω)...")
        test_results = []

        # 扫描上限范围为 0 到 100000Ω，按 50Ω 步进
        for r_val in range(0, 100001, 50):
            resistor.set_resistance(r_val)

            # 清除旧的 0x85 接收缓存，以便在此延迟内捕获最新发出的 0x85 报文
            with msg_85_lock:
                latest_msg_85 = None

            # 调完阻值后等待 2s
            time.sleep(2.0)

            # 读取 AIN 阻值测量 DID [0x22, 0x02, 0x54]
            resp = execute_service(zcan, chn_handle, "Read AIN Value (22 02 54)", [0x22, 0x02, 0x54])
            ain_data = {}
            if resp and len(resp) >= 21:
                # 提取 Byte3 开始的 18个字节 (AIN1 ~ AIN9)
                for i in range(9):
                    idx = 3 + i * 2
                    val = (resp[idx] << 8) | resp[idx + 1]
                    ain_data[f"AIN{i+1}"] = val
            else:
                log_and_print(f"[警告] 设定阻值 {r_val} Ω 时，读取 AIN 通道数据无应答。")

            # 读取并解析 0x85 报文
            msg_85_data = None
            with msg_85_lock:
                if latest_msg_85 is not None:
                    msg_85_data = list(latest_msg_85)

            # 声明校验用变量
            r_vol_ain_on = 0
            r_vol_ain_off = 0
            r_i_ain_on = 0
            ain_res = 0
            u32_temp = 0.0

            if msg_85_data and len(msg_85_data) >= 8:
                # 默认大端格式 (MSB) 提取 16bit 参数
                r_vol_ain_on = (msg_85_data[0] << 8) | msg_85_data[1]
                r_vol_ain_off = (msg_85_data[2] << 8) | msg_85_data[3]
                r_i_ain_on = (msg_85_data[4] << 8) | msg_85_data[5]
                ain_res = (msg_85_data[6] << 8) | msg_85_data[7]

                # 校验计算公式 (防 0 除保护)
                if r_i_ain_on != 0:
                    u32_temp = (r_vol_ain_on - r_vol_ain_off) * 7.125 * 83.3 / r_i_ain_on
                else:
                    u32_temp = 0.0

                validation_line = (
                    f"[0x85校验] 设定阻值: {r_val} Ω | "
                    f"R_Vol_AIN_ON: {r_vol_ain_on} | "
                    f"R_Vol_AIN_OFF: {r_vol_ain_off} | "
                    f"R_I_AIN_ON: {r_i_ain_on} | "
                    f"U32temp(计算值): {u32_temp:.2f} | "
                    f"AIN_res(报文值): {ain_res}"
                )
                log_and_print(validation_line)
            else:
                log_and_print(f"[警告] 设定阻值 {r_val} Ω 时，未捕获到最新的 0x85 报文。")

            # 整合所有记录到 CSV 缓存
            record = {
                "Target_Resistance_Ohm": r_val,
                "R_Vol_AIN_ON": r_vol_ain_on,
                "R_Vol_AIN_OFF": r_vol_ain_off,
                "R_I_AIN_ON": r_i_ain_on,
                "U32temp_Calculated": round(u32_temp, 2),
                "AIN_res_Reported": ain_res
            }
            # 如果诊断 AIN 读取有数据，追加
            record.update(ain_data)
            test_results.append(record)

            # 控制台输出 AIN 状态并记录
            if ain_data:
                log_line = f"[记录] AIN数据: " + ", ".join([f"AIN{k}:{v}" for k, v in enumerate(ain_data.values(), 1)])
                log_and_print(log_line)

        # 6. 数据保存和收尾
        log_and_print("[3/3] 测试完毕，保存数据并关闭模块连接。")
        if test_results:
            csv_file = os.path.join(data_dir, f"ain_standalone_sweep_results_{timestamp}.csv")
            with open(csv_file, 'w', newline='', encoding='utf-8') as f:
                # 自动动态提取所有字典 Key 作为 CSV 表头
                field_names = test_results[0].keys()
                writer = csv.DictWriter(f, fieldnames=field_names)
                writer.writeheader()
                writer.writerows(test_results)
            log_and_print(f"[SUCCESS] 矩阵 CSV 数据已成功保存到文件: {os.path.abspath(csv_file)}")
            log_and_print(f"[SUCCESS] AIN 测试日志已成功保存到文件: {os.path.abspath(sweep_log_file)}")

    except Exception as e:
        print(f"[CRITICAL] 测试过程中捕获到异常退出: {e}")
    finally:
        # 关闭后台线程与硬件资源
        thread_flag = False
        rx_thread.join(timeout=1.0)
        stop_hardware_auto_send(zcan, dev_handle, CHANNEL_INDEX)
        zcan.ResetCAN(chn_handle)
        zcan.CloseDevice(dev_handle)
        resistor.close()
        print("[CLEAR] 串口与 CAN 物理通道已安全回收并关闭。")


if __name__ == "__main__":
    run_test()