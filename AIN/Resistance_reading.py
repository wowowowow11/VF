""" ****************************************************************************
**    Author:  Waythink Test Engineering Team                                 **
**    Date:    2026.07.01                                                     **
**    Version: 2.5.0.0 (Read AIN Resistance for Exactly 10 Minutes)           **
**    Project: VF6NP - Standalone Auto AIN Resistance Read Test               **
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

# 安全访问配置
dll_path = os.path.join(current_dir, "VF3SeedToKey.dll")

# 全局变量控制后台接收
thread_flag = True
uds_queue = queue.Queue()
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


# ==================== ZLG CAN 后台接收线程 ====================
def receive_thread_func(zcan, chn_handle):
    global thread_flag
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
        time.sleep(0.005)


# ==================== 对齐 cdscoding 稳定款 UDS-ISO-TP 收发逻辑 ====================
def send_uds_raw(zcan, chn_handle, req_id, payload_8bytes):
    tx_obj = ZCAN_Transmit_Data()
    tx_obj.frame.can_id = req_id
    tx_obj.frame.can_dlc = 8
    tx_obj.transmit_type = 0  # 正常发送
    for i in range(8):
        tx_obj.frame.data[i] = payload_8bytes[i]

    # 打印底层发送报文 (TX Log)
    tx_hex = " ".join([f"{b:02X}" for b in payload_8bytes])
    print(f"[TX] ID: 0x{req_id:03X} | DATA: {tx_hex}")
    return zcan.Transmit(chn_handle, tx_obj, 1) == 1


def wait_uds_response(timeout=2.0):
    try:
        data = uds_queue.get(timeout=timeout)
        # 打印底层接收报文 (RX Log)
        rx_hex = " ".join([f"{b:02X}" for b in data])
        print(f"[RX] ID: 0x{UDS_RESP_ID:03X} | DATA: {rx_hex}")
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
    print(f"\n---> 诊断指令: {service_name} <---")

    # 清空可能存在的旧数据，防止残余帧污染
    while not uds_queue.empty():
        try:
            uds_queue.get_nowait()
        except:
            break

    # 1. 组包发送 (单帧 / 多帧)
    if len(payload) <= 7:
        can_data = [len(payload)] + payload
        while len(can_data) < 8:
            can_data.append(0x00)
        if not send_uds_raw(zcan, chn_handle, UDS_REQ_ID, can_data):
            return None
    else:
        total_len = len(payload)
        can_data = [0x10 | (total_len >> 8), total_len & 0xFF] + payload[:6]
        send_uds_raw(zcan, chn_handle, UDS_REQ_ID, can_data)

        fc = wait_uds_response(2.0)
        if not fc or (fc[0] >> 4) != 3:
            print("[ERROR] 未收到流控制帧(FC)")
            return None
        bs = fc[1]

        remain_payload = payload[6:]
        seq = 1
        bs_count = 0
        while remain_payload:
            chunk = remain_payload[:7]
            remain_payload = remain_payload[7:]
            cf_data = [0x20 | seq] + chunk
            while len(cf_data) < 8:
                cf_data.append(0x00)
            send_uds_raw(zcan, chn_handle, UDS_REQ_ID, cf_data)
            seq = (seq + 1) & 0x0F
            bs_count += 1
            if bs != 0 and bs_count >= bs and remain_payload:
                fc = wait_uds_response(2.0)
                if not fc or (fc[0] >> 4) != 3:
                    return None
                bs_count = 0
            time.sleep(0.01)

    # 2. 接收响应
    sid = payload[0]
    while True:
        resp = wait_uds_response(3.0)
        if not resp:
            print("[ERROR] 诊断响应超时")
            return None

        pci = resp[0] >> 4
        full_resp = None

        if pci == 0x0:
            full_resp = resp[1 : 1 + (resp[0] & 0x0F)]
        elif pci == 0x1:
            full_resp = parse_multi_frame_response(zcan, chn_handle, resp)

        if full_resp:
            if full_resp[0] == 0x7F and full_resp[1] == sid:
                if full_resp[2] == 0x78:
                    print(" 收到 NRC 78 响应挂起，继续等待...")
                    continue
                else:
                    print(f" 收到否定响应 (NRC): 0x{full_resp[2]:02X}")
                    return None
            elif full_resp[0] == (sid + 0x40):
                return full_resp
            else:
                return full_resp


# ==================== DBC 定时信号发送模块 ====================
def start_hardware_auto_send(zcan, dev_handle, chn):
    # 先清空当前通道的定时列表
    zcan.ZCAN_SetValue(dev_handle, f"{chn}/clear_auto_send", "0".encode("utf-8"))

    # 1. 0x342 ABS 10ms 帧
    abs_obj = ZCAN_AUTO_TRANSMIT_OBJ()
    abs_obj.enable = 1
    abs_obj.index = 0
    abs_obj.interval = 10
    abs_obj.obj.transmit_type = 0
    abs_obj.obj.frame.can_id = 0x342
    abs_obj.obj.frame.can_dlc = 8
    abs_data = [0x3F, 0x00, 0x00, 0x00, 0x80, 0x00, 0x00, 0x00]
    for i, v in enumerate(abs_data):
        abs_obj.obj.frame.data[i] = v
    zcan.ZCAN_SetValue(dev_handle, f"{chn}/auto_send", byref(abs_obj))

    # 2. 0x112 BCM CLAMP 100ms 帧
    bcm1_obj = ZCAN_AUTO_TRANSMIT_OBJ()
    bcm1_obj.enable = 1
    bcm1_obj.index = 1
    bcm1_obj.interval = 100
    bcm1_obj.obj.transmit_type = 0
    bcm1_obj.obj.frame.can_id = 0x112
    bcm1_obj.obj.frame.can_dlc = 8
    bcm1_data = [0x2D, 0x00, 0x00, 0x00, 0x02, 0x00, 0x00, 0x00]
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
    bcm2_data = [0x75, 0x00, 0xBB, 0x80, 0x00, 0x00, 0x00, 0x00]
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
    global seed, thread_flag
    print("=" * 60)
    print("      ACU AIN 阻值自读取测试上位机 (10分钟限时版)")
    print("=" * 60)

    # 1. 初始化 ZLG CAN 设备
    print("[1/5] 正在初始化 ZLG CAN 通讯卡...")
    zcan = ZCAN()
    dev_handle = zcan.OpenDevice(DEVICE_TYPE, DEVICE_INDEX, 0)
    if dev_handle == INVALID_DEVICE_HANDLE:
        print("[ERROR] 打开 CAN 卡设备失败！请确认硬件是否连接。")
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
        return

    if zcan.StartCAN(chn_handle) != ZCAN_STATUS_OK:
        print("[ERROR] 启动 CAN 通道失败。")
        zcan.CloseDevice(dev_handle)
        return

    print(f"CAN 通道 {CHANNEL_INDEX} 已成功启动，波特率: {CAN_BAUDRATE / 1000}kbps.")

    # 启动后台独立接收线程
    rx_thread = threading.Thread(target=receive_thread_func, args=(zcan, chn_handle))
    rx_thread.daemon = True
    rx_thread.start()

    # 准备数据保存目录
    data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    os.makedirs(data_dir, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    # 定义测试日志和CSV路径
    read_log_file = os.path.join(data_dir, f"ain_read_log_{timestamp}.log")
    csv_file = os.path.join(data_dir, f"ain_read_results_{timestamp}.csv")
    test_results = []

    try:
        # 2. 启动硬件周期帧仿真发送
        print("[2/5] 开始加载并执行硬件 DBC 周期帧仿真...")
        start_hardware_auto_send(zcan, dev_handle, CHANNEL_INDEX)

        # 3. 诊断解锁逻辑
        print("[3/5] 发起诊断 Extended 会话并计算安全密钥解锁...")
        # 3.1 会话切换
        resp = execute_service(zcan, chn_handle, "Diagnostic Session - Extended (10 03)", [0x10, 0x03])
        if not resp or resp[0] != 0x50:
            print("[WARNING] 切换 Extended Session 响应异常或无响应。")
        time.sleep(0.1)

        # 3.2 请求种子
        resp = execute_service(zcan, chn_handle, "Security Access - Request Seed (27 01)", [0x27, 0x01])
        if resp and len(resp) >= 18:
            seed = resp[2:18]
            print("[UDS] 获取种子成功! Seed: " + " ".join(f"{b:02X}" for b in seed))
        else:
            print("[ERROR] 无法获取安全种子 Seed。")
            raise RuntimeError("Seed request failed")
        time.sleep(0.1)

        # 3.3 密匙解锁
        key = call_zlgkey(seed, 1, "default")
        resp = execute_service(zcan, chn_handle, "Security Access - Send Key (27 02)", [0x27, 0x02] + key)
        if resp and resp[0] == 0x67:
            print("[UDS] 诊断安全解锁成功！")
        else:
            print("[ERROR] 诊断安全解锁失败。")
            raise RuntimeError("Unlock failed")
        time.sleep(0.1)

        # 3.4 激活写入
        execute_service(zcan, chn_handle, "Write Data By Identifier (2E 02 33 01 55)", [0x2E, 0x02, 0x33, 0x01, 0x55])
        time.sleep(0.5)

        # 4. AIN 电阻自读取核心循环 (限时 10 分钟 = 600 秒)
        duration_seconds = 300  # 10 分钟对应的秒数
        start_time = time.time()
        print(f"[4/5] 进入 AIN 阻值实时读取循环 (计划读取 10 分钟 / 600 秒，按 Ctrl+C 可提前结束并保存)...")

        while (time.time() - start_time) < duration_seconds:
            # 计算剩余时间
            elapsed_time = time.time() - start_time
            remaining_time = max(0, duration_seconds - elapsed_time)

            # 获取当前时间戳以作记录
            time_now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

            # 读取 AIN 阻值测量 DID [0x22, 0x02, 0x54]
            resp = execute_service(zcan, chn_handle, "Read AIN Value (22 02 54)", [0x22, 0x02, 0x54])
            if resp and len(resp) >= 21:
                # 提取 Byte3 开始的 18 个字节 (AIN1 ~ AIN9)
                ain_data = {}
                for i in range(9):
                    idx = 3 + i * 2
                    val = (resp[idx] << 8) | resp[idx + 1]
                    ain_data[f"AIN{i+1}"] = val

                # 暂存记录
                record = {"Timestamp": time_now, "Elapsed_Seconds": round(elapsed_time, 2)}
                record.update(ain_data)
                test_results.append(record)

                # 生成待写入与打印的目标字符串 (控制台增加剩余时间提醒)
                log_line = f"[{time_now}] [剩余时间: {int(remaining_time)}秒] " + ", ".join(
                    [f"AIN{k}:{v}" for k, v in enumerate(ain_data.values(), 1)])
                print(log_line)

                # 实时写入专属的 .log 文件
                try:
                    with open(read_log_file, "a", encoding="utf-8") as lf:
                        lf.write(log_line + "\n")
                except Exception as file_err:
                    print(f"[ERROR] 写入专属日志失败: {file_err}")
            else:
                print(f"[{time_now}] [剩余时间: {int(remaining_time)}秒] [警告] 读取 AIN 通道数据无应答。")

            # 每次读取间隔 1 秒
            time.sleep(0.02)

        print("\n[INFO] 10分钟读取时间已满，准备自动结束...")

    except KeyboardInterrupt:
        print("\n[INFO] 检测到键盘中断信号，准备提前退出并保存...")
    except Exception as e:
        print(f"[CRITICAL] 测试过程中捕获到异常退出: {e}")
    finally:
        # 5. 数据保存和收尾
        print("[5/5] 正在执行数据保存和通道关闭...")
        if test_results:
            try:
                with open(csv_file, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.DictWriter(f, fieldnames=test_results[0].keys())
                    writer.writeheader()
                    writer.writerows(test_results)
                print(f"[SUCCESS] CSV 汇总数据已成功保存: {os.path.abspath(csv_file)}")
                print(f"[SUCCESS] 纯净 AIN 日志已成功保存: {os.path.abspath(read_log_file)}")
            except Exception as save_err:
                print(f"[ERROR] 保存 CSV 文件失败: {save_err}")

        # 关闭后台线程与硬件资源
        thread_flag = False
        rx_thread.join(timeout=1.0)
        stop_hardware_auto_send(zcan, dev_handle, CHANNEL_INDEX)
        zcan.ResetCAN(chn_handle)
        zcan.CloseDevice(dev_handle)
        print("[CLEAR] CAN 物理通道已安全回收并关闭。测试结束。")


if __name__ == "__main__":
    run_test()