import os
import sys
import time
import threading
import logging
from datetime import datetime
from zlgcan import *

# ================= 1. 全局参数配置 =================
DEVICE_TYPE = ZCAN_USBCANFD_200U  # 设备型号 (例如: ZCAN_USBCAN2, ZCAN_USBCANFD_200U)
DEVICE_INDEX = 0  # 设备索引
CAN_CHANNEL = 0  # 测试通道 (0 代表 通道 1 / Port 1)
TARGET_CAN_ID = 0x488  # 目标采集报文 ID

# 正常基准字节（基准报文：C8 F5 E7 FF FF F8 FF FF）
EXPECTED_BYTE_3 = 0xE7  # 第 3 字节标准值 (索引 2)
EXPECTED_BYTE_6 = 0xF8  # 第 6 字节标准值 (索引 5)

# 波特率配置
NOMINAL_BAUD = "500000"  # 仲裁域波特率 500 Kbps
DATA_BAUD = "2000000"  # 数据域波特率 2.0 Mbps

# 创建 log 保存目录
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(CURRENT_DIR, "log")
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR, exist_ok=True)

# 日志文件名
log_filename = os.path.join(LOG_DIR, f"ACU_488_Collect_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

# ================= 2. 日志系统配置 =================
logger = logging.getLogger("ACU_488_Collector")
logger.setLevel(logging.INFO)

file_handler = logging.FileHandler(log_filename, encoding='utf-8')
console_handler = logging.StreamHandler()

formatter = logging.Formatter('%(asctime)s.%(msecs)03d %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
file_handler.setFormatter(formatter)
console_handler.setFormatter(formatter)

logger.addHandler(file_handler)
logger.addHandler(console_handler)

# 全局变量统计
thread_flag = True
total_msg_count = 0
abnormal_msg_count = 0
last_msg_time = None


# ================= 3. 数据校验与格式化处理 =================
def process_frame(can_type_str, can_id, dlc, data, delta_time_ms):
    global total_msg_count, abnormal_msg_count
    total_msg_count += 1

    is_abnormal = False
    change_details = []

    # 校验长度及第3字节(E7)、第6字节(F8)
    if len(data) >= 6:
        byte3_val = data[2]
        byte6_val = data[5]

        if byte3_val != EXPECTED_BYTE_3:
            is_abnormal = True
            change_details.append(f"第3字节(E7->{byte3_val:02X})")

        if byte6_val != EXPECTED_BYTE_6:
            is_abnormal = True
            change_details.append(f"第6字节(F8->{byte6_val:02X})")
    else:
        is_abnormal = True
        change_details.append(f"长度异常(DLC={dlc})")

    # 格式化数据字符串
    hex_data_str = " ".join([f"{b:02X}" for b in data])

    if is_abnormal:
        abnormal_msg_count += 1
        tag = "【异常】"
        detail_str = f" [{', '.join(change_details)}]"
        log_msg = (f"[RX] [{can_type_str:<5}] ID: 0x{can_id:03X} | DLC: {dlc:<2} | "
                   f"Data: {hex_data_str:<23} | Interval: {delta_time_ms:6.2f} ms | "
                   f"Total: {total_msg_count}  {tag}{detail_str}")
        logger.warning(log_msg)  # 异常输出高亮 Warning 级别
    else:
        log_msg = (f"[RX] [{can_type_str:<5}] ID: 0x{can_id:03X} | DLC: {dlc:<2} | "
                   f"Data: {hex_data_str:<23} | Interval: {delta_time_ms:6.2f} ms | "
                   f"Total: {total_msg_count}")
        logger.info(log_msg)


# ================= 4. 报文接收线程 =================
def receive_thread_func(zcanlib, chn_handle):
    global thread_flag, last_msg_time

    logger.info(f"===> 开始监听通道 CAN{CAN_CHANNEL} 上的 0x{TARGET_CAN_ID:03X} 报文 <===")
    logger.info(f"===> 异常监控基准: 第3字节期望 [E7], 第6字节期望 [F8] <===")

    while thread_flag:
        has_data = False

        # --- 1. 读取 CAN 2.0 报文 ---
        rcv_num = zcanlib.GetReceiveNum(chn_handle, ZCAN_TYPE_CAN)
        if rcv_num:
            read_cnt = min(rcv_num, 100)
            rcv_msg, actual_num = zcanlib.Receive(chn_handle, read_cnt, 50)
            for i in range(actual_num):
                frame = rcv_msg[i].frame
                can_id = frame.can_id & 0x1FFFFFFF

                if can_id == TARGET_CAN_ID:
                    has_data = True
                    current_time = time.time()
                    delta_time_ms = (current_time - last_msg_time) * 1000 if last_msg_time else 0.0
                    last_msg_time = current_time

                    dlc = frame.can_dlc
                    data = [frame.data[j] for j in range(dlc)]
                    process_frame("CAN", can_id, dlc, data, delta_time_ms)

        # --- 2. 读取 CANFD 报文 ---
        rcv_fd_num = zcanlib.GetReceiveNum(chn_handle, ZCAN_TYPE_CANFD)
        if rcv_fd_num:
            read_cnt = min(rcv_fd_num, 100)
            rcv_fd_msg, actual_num = zcanlib.ReceiveFD(chn_handle, read_cnt, 50)
            for i in range(actual_num):
                frame = rcv_fd_msg[i].frame
                can_id = frame.can_id & 0x1FFFFFFF

                if can_id == TARGET_CAN_ID:
                    has_data = True
                    current_time = time.time()
                    delta_time_ms = (current_time - last_msg_time) * 1000 if last_msg_time else 0.0
                    last_msg_time = current_time

                    dlc = frame.len
                    data = [frame.data[j] for j in range(dlc)]
                    process_frame("CANFD", can_id, dlc, data, delta_time_ms)

        if not has_data:
            time.sleep(0.002)


# ================= 5. 主程序入口 =================
def main():
    global thread_flag
    zcanlib = ZCAN()

    logger.info("==================================================")
    logger.info("      周立功 CAN 报文采集与异常监测 (ACU 0x488)     ")
    logger.info("==================================================")
    logger.info(f"保存日志文件至: {log_filename}")

    # 打开设备
    logger.info(f"正在打开设备 (DeviceType: {DEVICE_TYPE.value}, Index: {DEVICE_INDEX})...")
    handle = zcanlib.OpenDevice(DEVICE_TYPE, DEVICE_INDEX, 0)
    if handle == INVALID_DEVICE_HANDLE:
        logger.error("打开 CAN 设备失败！请检查硬件连接或驱动。")
        return

    try:
        # 配置波特率与内部终端电阻
        zcanlib.ZCAN_SetValue(handle, f"{CAN_CHANNEL}/canfd_abit_baud_rate", NOMINAL_BAUD.encode("utf-8"))
        zcanlib.ZCAN_SetValue(handle, f"{CAN_CHANNEL}/canfd_dbit_baud_rate", DATA_BAUD.encode("utf-8"))
        zcanlib.ZCAN_SetValue(handle, f"{CAN_CHANNEL}/initenal_resistance", "1".encode("utf-8"))

        # 初始化并启动通道
        chn_init_cfg = ZCAN_CHANNEL_INIT_CONFIG()
        chn_init_cfg.can_type = ZCAN_TYPE_CANFD
        chn_init_cfg.config.canfd.mode = 0

        chn_handle = zcanlib.InitCAN(handle, CAN_CHANNEL, chn_init_cfg)
        if chn_handle is None:
            logger.error(f"初始化通道 CAN{CAN_CHANNEL} 失败！")
            return

        if zcanlib.StartCAN(chn_handle) != ZCAN_STATUS_OK:
            logger.error(f"启动通道 CAN{CAN_CHANNEL} 失败！")
            return

        logger.info(f"通道 CAN{CAN_CHANNEL} 启动成功，波特率: {NOMINAL_BAUD}/{DATA_BAUD}")

        # 启动接收线程
        rx_thread = threading.Thread(target=receive_thread_func, args=(zcanlib, chn_handle))
        rx_thread.daemon = True
        rx_thread.start()

        logger.info("实时采集与监控中... 按 [Ctrl + C] 或 Enter 键终止采集。\n")

        try:
            input(">>> 按回车键 (Enter) 停止采集 <<<\n")
        except KeyboardInterrupt:
            logger.info("\n捕获到中断信号 (Ctrl+C)...")

    except Exception as e:
        logger.error(f"发生异常: {e}")
    finally:
        logger.info("正在停止采集并关闭设备...")
        thread_flag = False
        time.sleep(0.5)

        if 'chn_handle' in locals() and chn_handle:
            zcanlib.ResetCAN(chn_handle)
        if 'handle' in locals() and handle != INVALID_DEVICE_HANDLE:
            zcanlib.CloseDevice(handle)

        logger.info("==================================================")
        logger.info(f"采集结束！汇总如下:")
        logger.info(f"  - 接收 0x{TARGET_CAN_ID:03X} 总帧数 : {total_msg_count}")
        logger.info(f"  - 触发【异常】标记帧数 : {abnormal_msg_count}")
        logger.info(f"日志文件保存至: {log_filename}")
        logger.info("==================================================")


if __name__ == "__main__":
    main()