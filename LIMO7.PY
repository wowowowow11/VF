import os
import sys
import time
import queue
import threading
import ctypes  # 引入 ctypes 提升 Windows 物理定时器精度
from datetime import datetime
from zlgcan import *

# ================= 提升系统物理时钟精度 (1ms 级别) =================
try:
    # 提升 Windows 系统的时钟中断精度到 1 毫秒 (解决 time.sleep 默认 15.6ms 的时间延迟限制)
    ctypes.windll.winmm.timeBeginPeriod(1)
    print("[INFO] Windows 系统定时器精度已成功提升至 1 毫秒。")
except Exception as e:
    print(f"[WARNING] 无法提升 Windows 定时器精度: {e}")

# ================= 硬件连接与通道配置 =================
DEVICE_TYPE = ZCAN_USBCANFD_200U  # 设备类型
TARGET_PORT = 0  # 目标通道 (0 代表 Port 1)
BAUDRATE = "500000"  # 波特率 (500k)
ENABLE_TERMINAL_RESISTOR = "1"  # 120 欧终端电阻 (1:开启, 0:关闭)

# ================= ASC 文件配置 =================
ASC_FILE_PATH = r"F:\Python3.10\LIMO7\can_20260709094603.asc"

# ================= 播放速度微调配置 =================
PLAYBACK_SPEED_FACTOR = 1

# ================= 自定义 CAN ID 周期配置 (单位: 秒) =================
CUSTOM_PERIOD_CONFIG = {
    0xC9:  0.4,  # 400ms
    0x10A: 0.4,  # 400ms
    0x125: 0.4,  # 400ms
    0x132: 0.4,  # 400ms
    0x166: 0.4,  # 400ms
    0x167: 0.4,  # 400ms
    0x17E: 0.4,  # 400ms
    0x270: 0.4,  # 400ms
    0x29E: 4,    # 4000ms
    0x32E: 0.4,  # 400ms
    0x3E2: 0.4,  # 400ms
    0x378: 0.4,  # 400ms
}

# ================= 日志与过滤配置 =================
LOG_DIR = r"./log"  # 日志存储目录
TARGET_FILTER_ID = 0x80  # 【修改点 1】专属日志过滤的目标 ID 调整为 0x80

# ================= UDS 诊断配置 =================
UDS_REQ_ID = 0x688  # 物理请求 ID (诊断请求)
UDS_RESP_ID = 0x608  # ECU 响应 ID (诊断响应)
UDS_READ_COOLDOWN = 3.0  # 故障触发读取的冷却时间限制 (秒)

# ================= 全局变量与锁 =================
thread_flag = True
log_lock = threading.Lock()  # 线程锁，确保多线程写入安全
all_log_file = None  # 全量日志文件句柄
filter_log_file = None  # 80 专属日志文件句柄

uds_cleared = False
last_uds_read_time = 0.0
start_wall_time = 0.0
anomaly_detected = False

uds_queue = queue.Queue()
log_queue = queue.Queue()

zcanlib_global = None
chn_handle_global = None


def init_log_files():
    """
    初始化日志文件，自动创建日志文件夹
    """
    global all_log_file, filter_log_file
    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR, exist_ok=True)

    time_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    all_log_path = os.path.join(LOG_DIR, f"CAN_Trace_All_{time_str}.log")
    filter_log_path = os.path.join(LOG_DIR, f"CAN_Trace_80Only_{time_str}.log")

    all_log_file = open(all_log_path, "w", encoding="utf-8")
    filter_log_file = open(filter_log_path, "w", encoding="utf-8")

    print("\n" + "=" * 65)
    print(f"[INFO] 日志文件创建成功:\n 1. 全量日志 (All): {all_log_path}\n 2. 80 专属日志  : {filter_log_path}")
    print("=" * 65 + "\n")


def write_log(direction, can_id, data_bytes):
    """
    统一日志处理函数：负责故障/正响应判定 + 联动触发 UDS 读取。
    """
    global uds_cleared, last_uds_read_time, anomaly_detected

    time_str = datetime.now().strftime('%m-%d %H:%M:%S.%f')[:-3]
    dir_map = "Tx" if "TX" in direction else "Rx"
    channel = TARGET_PORT + 1
    hex_data_str = " ".join([f"{x:02x}" for x in data_bytes])

    log_line = f"{time_str} {channel} {can_id:x} {dir_map} d {len(data_bytes)} {hex_data_str} "

    # 【修改点 2】判定 0x80 报文的第二个字节 (data_bytes[1]) 是否在 0x10 ~ 0x1E 范围内
    if can_id == TARGET_FILTER_ID and len(data_bytes) >= 2:
        second_byte = data_bytes[1]
        if 0x10 <= second_byte <= 0x1E:
            log_line += " <=== [出现故障了]"
            anomaly_detected = True  # 触发全局异常中断

            if uds_cleared:
                now = time.time()
                if now - last_uds_read_time >= UDS_READ_COOLDOWN:
                    last_uds_read_time = now
                    threading.Thread(target=trigger_uds_read, daemon=True).start()

    if direction == "RX_UDS" and len(data_bytes) > 0:
        if data_bytes[0] in [0x54, 0x59]:
            log_line += " <=== [回复正响应]"

    log_line += "\n"
    log_queue.put((log_line, can_id))


def log_writer_thread_func():
    """
    后台日志写入线程
    """
    global thread_flag, all_log_file, filter_log_file
    while thread_flag or not log_queue.empty():
        try:
            log_item = log_queue.get(timeout=0.1)
            log_line, can_id = log_item

            print(log_line.strip())

            with log_lock:
                if all_log_file and not all_log_file.closed:
                    all_log_file.write(log_line)
                    all_log_file.flush()

                if can_id == TARGET_FILTER_ID:
                    if filter_log_file and not filter_log_file.closed:
                        filter_log_file.write(log_line)
                        filter_log_file.flush()

            log_queue.task_done()
        except queue.Empty:
            continue


def parse_asc_file(file_path):
    """
    解析 Vector ASC 文件
    """
    messages = []
    is_hex = True

    if not os.path.exists(file_path):
        print(f"[ERROR] 未找到 ASC 文件: {file_path}")
        sys.exit(1)

    print(f"[INFO] 正在解析 ASC 文件: {file_path} ...")

    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('//') or line.startswith('date'):
                continue

            if line.startswith('base'):
                if 'dec' in line.lower():
                    is_hex = False
                continue

            parts = line.split()
            if len(parts) < 6:
                continue

            try:
                timestamp = float(parts[0])
                can_id_str = parts[2]
                is_extended = False
                if can_id_str.endswith('x') or can_id_str.endswith('X'):
                    is_extended = True
                    can_id_str = can_id_str[:-1]

                can_id = int(can_id_str, 16 if is_hex else 10)
                dlc = int(parts[5])

                data_bytes = []
                for i in range(dlc):
                    data_bytes.append(int(parts[6 + i], 16 if is_hex else 10))

                messages.append({
                    'timestamp': timestamp,
                    'can_id': can_id,
                    'is_extended': is_extended,
                    'dlc': dlc,
                    'data': data_bytes
                })
            except (ValueError, IndexError):
                continue

    print(f"[SUCCESS] 解析完成，共提取到 {len(messages)} 条 CAN 报文。")
    return messages


def apply_custom_periods(messages, period_config):
    """
    对指定的 CAN ID 进行自定义周期重采样，其余 ID 保持 ASC 原始时间间隔
    """
    if not messages or not period_config:
        return messages

    t_max = messages[-1]['timestamp']

    normal_messages = []
    target_messages_by_id = {}

    for msg in messages:
        can_id = msg['can_id']
        if can_id in period_config:
            if can_id not in target_messages_by_id:
                target_messages_by_id[can_id] = []
            target_messages_by_id[can_id].append(msg)
        else:
            normal_messages.append(msg)

    generated_messages = []
    for can_id, period in period_config.items():
        orig_msgs = target_messages_by_id.get(can_id, [])
        if not orig_msgs:
            print(f"[WARNING] 配置的 ID 0x{can_id:X} 在 ASC 文件中未发现，跳过重采样。")
            continue

        payload_pool = [m['data'] for m in orig_msgs]
        is_extended = orig_msgs[0]['is_extended']

        curr_t = orig_msgs[0]['timestamp']
        idx = 0
        count = 0

        while curr_t <= t_max:
            payload = payload_pool[idx % len(payload_pool)]
            generated_messages.append({
                'timestamp': curr_t,
                'can_id': can_id,
                'is_extended': is_extended,
                'dlc': len(payload),
                'data': payload
            })
            curr_t += period
            idx += 1
            count += 1

        print(f"[INFO] 已将 ID 0x{can_id:X} 修改为 {period * 1000:.0f}ms 周期 (由原 {len(orig_msgs)} 帧重采样为 {count} 帧)")

    all_messages = normal_messages + generated_messages
    all_messages.sort(key=lambda x: x['timestamp'])

    print(f"[SUCCESS] 报文时间序列重构完成，最终重放队列共 {len(all_messages)} 条报文。")
    return all_messages


def send_can_frame(zcanlib, chn_handle, can_id, data_list, is_extended=False):
    """
    调用 zlgcan 发送单条报文
    """
    msgs = (ZCAN_Transmit_Data * 1)()
    msgs[0].transmit_type = 0

    if is_extended:
        msgs[0].frame.can_id = can_id | 0x80000000
    else:
        msgs[0].frame.can_id = can_id

    msgs[0].frame.can_dlc = len(data_list)
    for i in range(len(data_list)):
        msgs[0].frame.data[i] = data_list[i]

    return zcanlib.Transmit(chn_handle, msgs, 1) == 1


# ================= ISO-TP 流控制与诊断核心 =================

def wait_uds_response(timeout=3.0):
    try:
        return uds_queue.get(timeout=timeout)
    except queue.Empty:
        return None


def execute_uds_service(zcanlib, chn_handle, payload):
    dlc = len(payload)
    can_data = [dlc] + payload
    while len(can_data) < 8:
        can_data.append(0x00)

    while not uds_queue.empty():
        try:
            uds_queue.get_nowait()
        except queue.Empty:
            break

    write_log("TX_UDS", UDS_REQ_ID, can_data)
    if not send_can_frame(zcanlib, chn_handle, UDS_REQ_ID, can_data):
        return None

    first_resp = wait_uds_response(2.0)
    if not first_resp:
        return None

    pci = first_resp[0] >> 4

    if pci == 0x0:
        resp_len = first_resp[0] & 0x0F
        full_resp = first_resp[1: 1 + resp_len]
        write_log("RX_UDS", UDS_RESP_ID, full_resp)
        return full_resp

    elif pci == 0x1:
        total_len = ((first_resp[0] & 0x0F) << 8) | first_resp[1]
        received_data = first_resp[2:]

        fc_data = [0x30, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]
        write_log("TX_FC", UDS_REQ_ID, fc_data)
        send_can_frame(zcanlib, chn_handle, UDS_REQ_ID, fc_data)

        start_time = time.time()
        while len(received_data) < total_len and (time.time() - start_time < 4.0):
            cf = wait_uds_response(1.0)
            if not cf:
                break
            cf_pci = cf[0] >> 4
            if cf_pci == 0x2:
                received_data.extend(cf[1:])

        full_resp = received_data[:total_len]
        write_log("RX_UDS", UDS_RESP_ID, full_resp)
        return full_resp

    else:
        write_log("RX_UDS_RAW", UDS_RESP_ID, first_resp)
        return first_resp


def trigger_uds_read():
    global zcanlib_global, chn_handle_global
    if zcanlib_global and chn_handle_global:
        execute_uds_service(zcanlib_global, chn_handle_global, [0x19, 0x02, 0xFF])


def receive_thread_func(zcanlib, chn_handle):
    global thread_flag
    print("[INFO] 后台接收监听已启动...")

    while thread_flag:
        rcv_num = zcanlib.GetReceiveNum(chn_handle, ZCAN_TYPE_CAN)
        if rcv_num:
            read_cnt = min(rcv_num, 100)
            rcv_msg, actual_num = zcanlib.Receive(chn_handle, read_cnt, 50)
            for i in range(actual_num):
                frame = rcv_msg[i].frame
                can_id = frame.can_id & 0x1FFFFFFF
                dlc = frame.can_dlc
                data = [frame.data[j] for j in range(dlc)]

                if can_id == UDS_RESP_ID:
                    uds_queue.put(data)
                else:
                    write_log("RX", can_id, data)

        rcv_num_fd = zcanlib.GetReceiveNum(chn_handle, ZCAN_TYPE_CANFD)
        if rcv_num_fd:
            read_cnt = min(rcv_num_fd, 100)
            rcv_msg, actual_num = zcanlib.ReceiveFD(chn_handle, read_cnt, 50)
            for i in range(actual_num):
                frame = rcv_msg[i].frame
                can_id = frame.can_id & 0x1FFFFFFF
                dlc = frame.len
                data = [frame.data[j] for j in range(dlc)]

                if can_id == UDS_RESP_ID:
                    uds_queue.put(data)
                else:
                    write_log("RX_FD", can_id, data)

        time.sleep(0.002)


def periodic_uds_read_thread():
    global thread_flag, start_wall_time
    while thread_flag and start_wall_time == 0.0:
        time.sleep(0.1)

    next_read_time = start_wall_time + (30.0 / PLAYBACK_SPEED_FACTOR)

    while thread_flag:
        now = time.time()
        if now >= next_read_time:
            target_60s_offset = 60.0 / PLAYBACK_SPEED_FACTOR
            if abs((next_read_time - start_wall_time) - target_60s_offset) < 1.0:
                next_read_time += (30.0 / PLAYBACK_SPEED_FACTOR)
                continue

            print("\n>>> [UDS] 定时触发：每 30 秒执行一次 UDS 读取 (19 02 FF) <<<")
            trigger_uds_read()
            next_read_time += (30.0 / PLAYBACK_SPEED_FACTOR)

        time.sleep(0.2)


def main():
    global thread_flag, zcanlib_global, chn_handle_global, uds_cleared, last_uds_read_time, start_wall_time, anomaly_detected

    loop_counter = 0

    # 1. 解析 ASC 文件
    messages = parse_asc_file(ASC_FILE_PATH)
    if not messages:
        print("[WARNING] 文件中未解析到任何有效报文，程序退出。")
        return

    # 2. 应用指定 ID 的周期修改
    messages = apply_custom_periods(messages, CUSTOM_PERIOD_CONFIG)

    # 3. 初始化日志文件
    init_log_files()

    # 4. 初始化 ZLG CAN 硬件
    zcanlib = ZCAN()
    print("正在打开 ZLG CAN 设备...")
    handle = zcanlib.OpenDevice(DEVICE_TYPE, 0, 0)
    if handle == INVALID_DEVICE_HANDLE:
        print("[ERROR] 打开设备失败！请检查物理连接。")
        return

    zcanlib.ZCAN_SetValue(handle, f"{TARGET_PORT}/canfd_abit_baud_rate", BAUDRATE.encode("utf-8"))
    zcanlib.ZCAN_SetValue(handle, f"{TARGET_PORT}/initenal_resistance", ENABLE_TERMINAL_RESISTOR.encode("utf-8"))

    chn_init_cfg = ZCAN_CHANNEL_INIT_CONFIG()
    chn_init_cfg.can_type = ZCAN_TYPE_CANFD
    chn_init_cfg.config.canfd.mode = 0

    print(f"正在初始化通道 Port {TARGET_PORT + 1}...")
    chn_handle = zcanlib.InitCAN(handle, TARGET_PORT, chn_init_cfg)

    if zcanlib.StartCAN(chn_handle) != ZCAN_STATUS_OK:
        print("[ERROR] 启动通道失败！")
        zcanlib.CloseDevice(handle)
        return

    zcanlib_global = zcanlib
    chn_handle_global = chn_handle

    # 5. 启动后台线程
    rx_thread = threading.Thread(target=receive_thread_func, args=(zcanlib, chn_handle))
    rx_thread.daemon = True
    rx_thread.start()

    periodic_thread = threading.Thread(target=periodic_uds_read_thread, daemon=True)
    periodic_thread.start()

    log_writer_thread = threading.Thread(target=log_writer_thread_func, daemon=True)
    log_writer_thread.start()

    print("\n" + "=" * 65)
    print(" 启动运行：[TX] 重放报文 | [RX] ACU 自发报文")
    print(" 循环播放模式已开启，将持续重复播放直到检测到 0x80 异常帧中断")
    print(" 按 [Ctrl + C] 可提前终止并安全保存")
    print("=" * 65 + "\n")

    # 6. 循环重放精准时序逻辑
    start_wall_time_initial = time.time()
    loop_counter = 1

    try:
        while not anomaly_detected:
            print(f"\n" + "=" * 65)
            print(f" >>> 开始执行第 {loop_counter} 轮 ASC 报文重放 <<< ")
            print("=" * 65)

            start_wall_time = time.time()
            first_msg_time = messages[0]['timestamp']

            for idx, msg in enumerate(messages):
                if anomaly_detected:
                    break

                current_time = time.time()

                # 1 分钟定时触发 UDS 诊断
                target_60s_offset = 60.0 / PLAYBACK_SPEED_FACTOR
                if not uds_cleared and (current_time - start_wall_time_initial >= target_60s_offset):
                    uds_cleared = True
                    print("\n>>> [UDS] 启动已满 1 分钟，执行 ISO-TP 诊断 <<<")

                    def init_diag_task():
                        execute_uds_service(zcanlib, chn_handle, [0x14, 0xFF, 0xFF, 0xFF])
                        time.sleep(0.5)
                        execute_uds_service(zcanlib, chn_handle, [0x19, 0x02, 0xFF])

                    threading.Thread(target=init_diag_task, daemon=True).start()
                    last_uds_read_time = time.time()

                # 发送时间对齐
                target_offset = (msg['timestamp'] - first_msg_time) / PLAYBACK_SPEED_FACTOR
                target_send_time = start_wall_time + target_offset

                sleep_duration = target_send_time - time.time()
                if sleep_duration > 0:
                    time.sleep(sleep_duration)

                # 发送报文
                success = send_can_frame(
                    zcanlib,
                    chn_handle,
                    msg['can_id'],
                    msg['data'],
                    msg['is_extended']
                )

                if success:
                    write_log("TX", msg['can_id'], msg['data'])
                else:
                    write_log("TX_FAIL", msg['can_id'], msg['data'])

            if anomaly_detected:
                break

            loop_counter += 1
            time.sleep(0.1)

        # 7. 异常退出后的故障捕获诊断
        if anomaly_detected:
            print(f"\n" + "=" * 65)
            print(f" [SUCCESS] 成功检测到 0x80 异常帧（第二个字节范围: 0x10~0x1E）！")
            print(f" 重放已即时停止。正在发起即时 UDS 故障码读取 (19 02 FF) 捕获现场...")
            print("=" * 65 + "\n")

            trigger_uds_read()
            time.sleep(5.0)

    except KeyboardInterrupt:
        print("\n[INFO] 用户手动终止了程序。")
    finally:
        print("\n正在清理资源并关闭设备与日志文件...")
        thread_flag = False
        rx_thread.join(timeout=1.0)

        with log_lock:
            if all_log_file:
                all_log_file.close()
            if filter_log_file:
                filter_log_file.close()

        zcanlib.ResetCAN(chn_handle)
        zcanlib.CloseDevice(handle)
        print("[SUCCESS] 设备与日志文件均已安全关闭并保存。")
        print(f"[INFO] 本次测试运行已结束，共循环运行了 {loop_counter} 轮 ASC 报文重放。")


if __name__ == "__main__":
    main()