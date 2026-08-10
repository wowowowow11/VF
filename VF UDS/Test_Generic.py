# -*- coding: utf-8 -*-
import os
import yaml
import time
import pytest
import allure
from Conftest import uds_client
from UDS_client import HexList


def parse_payload(payload_data):
    """
    通用数据解析：支持处理列表中的 ['0x02', '0x10']、[0x02, 0x10] 及空格分隔的十六进制字符串
    """
    if not payload_data:
        return []
    if isinstance(payload_data, list):
        return [int(str(x), 16) if isinstance(x, str) else x for x in payload_data]
    if isinstance(payload_data, str):
        return [int(x, 16) for x in payload_data.strip().split()]
    return []


def load_yaml_cases():
    """
    自动扫描并读取 output_yamls 下的用例文件。
    支持通过环境变量 RUN_YAML 进行文件级过滤。
    【用例过滤升级】：检查 PositiveResponse 和 NegativeResponse 的 Enable 标志。
    如果 Enable 设为 0 / false，则动态跳过整个测试类型的用例收集。
    """
    current_dir = os.path.dirname(os.path.abspath(__file__))
    yaml_folder = os.path.join(current_dir, "output_yamls")

    if not os.path.exists(yaml_folder):
        yaml_folder = current_dir

    yaml_paths = [
        os.path.join(yaml_folder, f)
        for f in os.listdir(yaml_folder)
        if f.endswith(".yaml") or f.endswith(".yml")
    ]
    yaml_paths.sort()

    # 从环境变量中读取需要跑的 YAML 文件过滤词
    run_yaml_filter = os.environ.get("RUN_YAML")

    all_cases = []
    for path in yaml_paths:
        file_name = os.path.basename(path).replace(".yaml", "").replace(".yml", "")

        # 如果配置了过滤环境变量，且当前文件名不包含该过滤词，则跳过
        if run_yaml_filter and run_yaml_filter not in file_name:
            continue

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except Exception as e:
            print(f"\n⚠️ 警告: 读取或解析 YAML 失败，已跳过该文件。文件: {path}, 错误: {e}")
            continue

        if not data:
            print(f"\n⚠️ 警告: YAML 文件为空，已跳过。文件: {path}")
            continue

        # 核心防御：如果读取到以 '-' 开头的不合规列表格式文件，直接跳过
        if isinstance(data, list):
            print(f"\n⚠️ 警告: 文件根节点为列表(List)格式，不符合套件字典规范，已跳过。文件: {path}")
            continue

        if not isinstance(data, dict):
            print(f"\n⚠️ 警告: 无法识别的 YAML 数据类型，已跳过。文件: {path}")
            continue

        can0_config = data.get("CAN0", {})
        if not isinstance(can0_config, dict):
            continue
        uds_section = can0_config.get("UDS", {})
        if not isinstance(uds_section, dict):
            continue

        for response_type in ["PositiveResponse", "NegativeResponse"]:
            group_data = uds_section.get(response_type, {})
            if not isinstance(group_data, dict):
                continue

            # 智能读取 Enable 参数。支持 1/0, true/false, "1"/"0"
            enable_val = group_data.get("Enable", 1)  # 缺省时默认启用 (1)
            if enable_val in [0, "0", False]:
                print(f"\nℹ️ 提示: 检测到套件 {file_name} 内的 [{response_type}] 'Enable' 被设为 0，已跳过整个用例组。")
                continue

            test_cases = group_data.get("TestCase", [])
            if not isinstance(test_cases, list):
                continue

            for case in test_cases:
                if isinstance(case, dict):
                    case["_file_name"] = file_name
                    case["_yaml_path"] = path
                    case["_response_type"] = response_type
                    case["_can0_config"] = can0_config
                    all_cases.append(case)

    return all_cases


def get_pytest_case_id(case):
    """
    智能生成 Pytest 参数化显示 ID。
    """
    file_name = case.get("_file_name", "Suite")
    case_id = case.get("id")
    response_type = case.get("_response_type", "UDS")

    if case_id:
        return f"{file_name}_{case_id}_{response_type}"

    # 备用方案：截取过长的 description
    description = case.get("description", "case")
    if len(description) > 50:
        description = description[:47] + "..."
    return f"{file_name}_{description}_{response_type}"


@allure.epic("ACU诊断自动化测试")
class TestGenericUds:
    _is_hardware_connected = False
    _last_service_id = None  # 类变量：用于记录上一条用例中最后执行完成的诊断服务 ID
    _last_resp_was_multi = False  # 类变量：用于智能识别上个用例的响应是否为多帧报文

    # 新增用例内部共享属性：用于安全解锁 $27 种子与等级在各个测试步骤（Step）间的实时流转
    _active_seed = None
    _active_seed_level = None

    @pytest.mark.parametrize("case", load_yaml_cases(), ids=get_pytest_case_id)
    def test_generic_uds_workflow(self, uds_client, case):
        """
        数据驱动通用诊断测试引擎（含智能多帧缓冲自适应冷却与 NVM 写入保护机制）
        """
        # ==================== 智能自适应过渡冷却延时 ====================
        if self.__class__._last_service_id == 0x14:
            # 如果上一个用例完成了清除故障码，给 ECU 留出 2.0s 充足时间将 DTC 擦写状态存入 Flash/EEPROM
            print(
                "\n[冷却保护] 监测到上个用例执行了 $14 (清除故障码)，因其写入 EEPROM 耗时较长，应用 2.0s 深度写入保护延时...")
            time.sleep(2.0)
        elif self.__class__._last_service_id == 0x11:
            # 如果上一个用例完成了复位，给 ECU 留出 4.0s 重启稳定期
            print("\n[冷却保护] 监测到上个用例执行了 $11 (ECU复位)，因其重启耗时较长，应用 4.0s 重启等待延时...")
            time.sleep(4.0)
        elif getattr(self.__class__, "_last_resp_was_multi", False):
            # 如果上一个用例最后回复了多帧（以 0x10-0x1F 开头的首帧），ECU 刚刚经历了密集的高频发送，
            # 给物理 ECU 留出 1.5s 的传输层缓冲空闲期，确保其协议栈安全重置完毕，规避偶数用例首步超时。
            print("\n[冷却保护] 监测到上个用例的响应为 UDS 多帧大包报文，应用 1.5s 传输层缓冲冷却延时...")
            time.sleep(1.5)
        else:
            # 普通用例过渡延时，规避 ECU 报 NRC 22 (条件不支持) 错误
            time.sleep(0.3)
        # ===============================================================

        case_id = case.get("id")
        description = case.get("description", "Unnamed Case")
        file_name = case.get("_file_name", "Suite")
        yaml_path = case.get("_yaml_path", "")
        response_type = case.get("_response_type", "UDS")
        can0_config = case.get("_can0_config", {})

        # ------------------ 1. 动态设置 Allure 视图分类与标题 ------------------
        project_name = can0_config.get("projectName", "VF6.PY")
        allure.dynamic.suite(f"Project: {project_name} | Diagnostics_Services: {file_name}")
        allure.dynamic.story(f"响应类型: {response_type}")

        # 如果存在 id，则将标题优化为 "[ID] 描述" 的高级格式
        if case_id:
            allure.dynamic.title(f"[{case_id}] {description}")
        else:
            allure.dynamic.title(f"{description}")

        # ------------------ 2. 动态读取并建立硬件连接 (含波特率) ------------------
        baud_rate = int(can0_config.get("baudRate", 500))
        if not self._is_hardware_connected:
            uds_client.connect(baud_rate=baud_rate)
            self.__class__._is_hardware_connected = True

        # ------------------ 3. 动态配置寻址 ID ------------------
        snd_physical_id = can0_config.get("sndPhysicalID")
        rcv_physical_id = can0_config.get("rcvPhysicalID")
        snd_function_id = can0_config.get("sndFunctionID")
        sa_lib_path = can0_config.get("saLibPath")

        if snd_physical_id:
            uds_client.req_id = int(snd_physical_id, 16)
        if rcv_physical_id:
            uds_client.resp_id = int(rcv_physical_id, 16)
        if snd_function_id:
            uds_client.func_id = int(snd_function_id, 16)

        # 动态处理安全算法库路径：将其解析为相对于当前正在执行的 yaml 文件的路径
        if sa_lib_path:
            if not os.path.isabs(sa_lib_path):
                yaml_dir = os.path.dirname(os.path.abspath(yaml_path))
                sa_lib_path = os.path.abspath(os.path.join(yaml_dir, sa_lib_path))
            uds_client.load_algorithm_dll(sa_lib_path)

        # ------------------ 4. 动态配置并启动底层周期性背景报文 (rcvNodeID) ------------------
        rcv_node_id = can0_config.get("rcvNodeID", [])
        if rcv_node_id:
            uds_client.setup_background_messages(rcv_node_id)
            time.sleep(0.3)
        else:
            uds_client.zcanlib.ZCAN_SetValue(uds_client.device_handle, f"{uds_client.port}/clear_auto_send", b"0")

        # 重置当前用例的 $27 解锁算法缓存，防止上一个用例的旧 Seed 干扰本轮计算
        self.__class__._active_seed = None
        self.__class__._active_seed_level = None

        # ------------------ 5. 执行测试指令 testInstructions.data ------------------
        test_instructions = case.get("testInstructions", {})
        steps = test_instructions.get("data", [])

        for idx, step in enumerate(steps, 1):
            # ==================== 1. 动态节点停发/控制/修改机制 ====================
            # 修改 Test_Generic.py 中的这一小段逻辑：
            modify_node_cfg = step.get("modifyNode")

            if isinstance(modify_node_cfg, list):
                # 💡 新增：如果是列表，循环一次性修改多个节点
                for item in modify_node_cfg:
                    if isinstance(item, dict):
                        uds_client.update_specific_node(
                            item.get("nodeID"),
                            new_data=item.get("data"),
                            new_period=item.get("period")
                        )
            elif isinstance(modify_node_cfg, dict):
                # 兼容原先单节点的写法
                uds_client.update_specific_node(
                    modify_node_cfg.get("nodeID"),
                    new_data=modify_node_cfg.get("data"),
                    new_period=modify_node_cfg.get("period")
                )
            else:
                node_id = None

            if node_id:
                uds_client.update_specific_node(node_id, new_data=new_data, new_period=new_period)

            # B. 关闭单个指定节点报文 (如 stopNode: "0x20D")
            stop_node = step.get("stopNode")
            if stop_node:
                uds_client.stop_specific_node(stop_node)

            # C. 关闭所有底层背景报文 (如 clearBackground: true)
            if step.get("clearBackground"):
                uds_client.clear_background_messages()

            # D. 恢复所有默认背景报文 (如 restoreNodes: true)
            if step.get("restoreNodes"):
                rcv_node_id_list = can0_config.get("rcvNodeID", [])
                uds_client.setup_background_messages(rcv_node_id_list)
            # =================================================================

            # 如果该步骤仅用于修改/控制节点，没有发送 UDS 指令 (没有 send)，跳过 UDS 处理
            if "send" not in step:
                delay_time_ms = int(step.get("delayTime", 0))
                if delay_time_ms > 0:
                    time.sleep(delay_time_ms / 1000.0)
                continue

            # 直接提取 8 字节底层发送帧
            send_payload = parse_payload(step.get("send"))
            recv_payload = parse_payload(step.get("recv"))

            # 寻址方式：isPhysicalID 值为 1 或 10 是物理寻址，其余为功能广播寻址
            is_physical = int(step.get("isPhysicalID", 1))
            addressing = "physical" if is_physical == 1 or is_physical == 10 else "functional"

            # 抑制正响应 / 无响应标志
            suppress_pos_rsp = int(step.get("suppressPosRspMsgIndicationBit", 0))
            is_send_no_response = int(step.get("isSendNoResponse", 0))
            expect_no_response = (suppress_pos_rsp == 1) or (is_send_no_response == 1)

            # 诊断命令发送后的测试延时 (单位: 毫秒)
            delay_time_ms = int(step.get("delayTime", 10))

            # ==================== 安全提取 SID 与 子功能 (防 IndexError) ====================
            is_multi = len(send_payload) > 2 and (send_payload[0] >> 4) == 0x1
            sid = send_payload[2] if (is_multi and len(send_payload) > 2) else (
                send_payload[1] if len(send_payload) > 1 else None)
            sub_func = send_payload[3] if (is_multi and len(send_payload) > 3) else (
                send_payload[2] if len(send_payload) > 2 else None)

            if sid == 0x27 and sub_func is not None and sub_func % 2 == 0:  # 偶数子功能：代表此步骤为“发送 Key(Send Key)”
                last_seed = self.__class__._active_seed
                last_level = self.__class__._active_seed_level

                if last_seed:
                    # 算法需要的等级一般为 Seed 请求等级 (即 Key 偶数等级 - 1)
                    calc_level = sub_func - 1
                    print(
                        f"\n[安全解锁] 检测到发送密钥步骤，正在计算 Key (Level {calc_level})... Seed: {HexList(last_seed)}")

                    # 调用 UDS_client 内集成的 ZLGKey 计算函数获取真实 Key
                    calculated_key = uds_client.call_zlgkey(last_seed, calc_level, "VF6.PY")
                    print(f"[安全解锁] 密钥计算成功: {HexList(calculated_key)}")

                    # 将计算出来的实际 Key 动态填入 send 报文的占位符中
                    # 对于单帧格式，Key 的起始写入索引是 3；对于多帧格式，起始索引是 4
                    key_start_idx = 4 if is_multi else 3

                    send_payload = list(send_payload)
                    for i, k_byte in enumerate(calculated_key):
                        target_idx = key_start_idx + i
                        if target_idx < len(send_payload):
                            send_payload[target_idx] = k_byte
                        else:
                            send_payload.append(k_byte)

                    print(f"[安全解锁] 动态密钥注入成功。发送整帧: {HexList(send_payload)}")
                else:
                    print("\n[安全解锁] ⚠️ 警告: 未找到对应的 Seed 缓存，将使用默认占位符发送。")
            # =================================================================================

            step_title = f"步骤 {idx}: 发送 {HexList(send_payload)} ({addressing})"
            with allure.step(step_title):
                # 性能控制：对于预期不回应的命令，缩短超时限制（0.15秒）
                timeout = 0.15 if expect_no_response else 3.0

                actual_resp = uds_client.execute_service(
                    service_name=f"Step_{idx}",
                    payload=send_payload,
                    addressing=addressing,
                    timeout=timeout
                )

                if expect_no_response:
                    # 断言无响应
                    allure.attach(f"Actual: {actual_resp}", "诊断接收结果 (预期不响应)")
                    assert actual_resp is None, f"步骤 {idx} 预期不响应，但实际收到了回复: {actual_resp}"
                else:
                    # 断言响应匹配（截取实际接收帧至预期长度进行对比，规避末尾填充字节如 CC 的干扰）
                    assert actual_resp is not None, f"步骤 {idx} 执行超时：未收到预期响应 {HexList(recv_payload)}"

                    sliced_resp = HexList(actual_resp[:len(recv_payload)])
                    allure.attach(
                        f"Expected: {HexList(recv_payload)}\nActual (Sliced): {sliced_resp}\nActual (Raw 8-Bytes): {actual_resp}",
                        "诊断接收结果")
                    assert sliced_resp == recv_payload, f"步骤 {idx} 诊断响应不匹配！预期: {HexList(recv_payload)}, 实际（去除填充）: {sliced_resp}"

                    # ==================== 核心新增：$27 随机种子（Seed）的自适应捕获 ====================
                    # 当接收正常，且 ECU 回复正响应（0x67），且子功能是奇数时，说明此步骤得到了随机种子
                    pci_rx = actual_resp[0] >> 4
                    if pci_rx == 0x1 and hasattr(actual_resp, "assembled_data"):
                        # 如果接收的是多帧响应，提取完整拼接数据（包含所有连续帧）
                        uds_payload = actual_resp.assembled_data
                    else:
                        # 如果接收的是单帧响应
                        uds_payload = actual_resp[1:1 + (actual_resp[0] & 0x0F)]

                    if len(uds_payload) >= 3 and uds_payload[0] == 0x67 and uds_payload[1] % 2 == 1:
                        # 提取并保存完整的随机种子字节列表和种子等级
                        self.__class__._active_seed = list(uds_payload[2:])
                        self.__class__._active_seed_level = uds_payload[1]
                        print(
                            f"\n[安全解锁] 成功捕获并保存 Seed: {HexList(self.__class__._active_seed)} (Level {self.__class__._active_seed_level})")
                    # =================================================================================

                # 步骤层级测试延时
                if delay_time_ms > 0:
                    time.sleep(delay_time_ms / 1000.0)

                # ------------------ 6. 后置记录：提取本用例最后执行完毕的服务状态 ------------------
                if steps:
                    last_send = parse_payload(steps[-1].get("send"))
                    last_recv = parse_payload(steps[-1].get("recv"))

                    if last_send:
                        # 记录最后一步发送的服务 ID (SID)
                        is_multi = len(last_send) > 2 and (last_send[0] >> 4) == 0x1
                        sid = last_send[2] if (is_multi and len(last_send) > 2) else (
                            last_send[1] if len(last_send) > 1 else None)
                        self.__class__._last_service_id = sid

                    if last_recv:
                        # 记录最后一步期望的回复是否是多帧首帧 (FF)
                        self.__class__._last_resp_was_multi = len(last_recv) > 0 and (last_recv[0] >> 4) == 0x1
                    else:
                        self.__class__._last_resp_was_multi = False