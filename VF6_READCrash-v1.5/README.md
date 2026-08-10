这是一份针对该项目的详细版 README.md 文件。本版本补充了底层的字节解析机制、UDS 诊断时序、动态库（DLL）的底层接口参数说明、以及在
ZCANPRO 运行时的调试与排查指南，方便开发和测试人员理解与二次开发。

VF65 碰撞数据提取与解析系统使用说明书

本系统是一套运行于 ZCANPRO
平台环境下的车载控制器（ECU）数据提取与解析方案。主要用于在碰撞测试或台架试验后，通过统一诊断服务（UDS）安全地从安全气囊控制器（ACU）或其他记录器中读取碰撞中间变量、物理传感器历史曲线（如加速度、压力变化），并将其还原为标准物理量。

1. 项目组成与文件关系

项目由三个核心文件组成，它们各司其职，协同完成“诊断连接 -> 安全解锁 -> 原始数据读取 -> 格式化解析 -> 本地化存储”的完整闭环。

├── config_data_type.json   # 静态数据结构配置文件（定义了每个变量在内存中的偏移量、长度及属性）
├── ReadCrashData.py        # 诊断时序控制与数据解析引擎（Python 3 脚本）
└── VF65ZLGDll.dll          # 安全访问算法动态链接库（提供控制器 Key 值的计算服务）

  - config_data_type.json：将复杂的 C 语言结构体映射为 JSON 格式。定义了
    FrontCrash（前碰）、SideCrashLH（左侧碰）、SideCrashRH（右侧碰）三个不同场景的数据结构映射表。
  - ReadCrashData.py：核心执行脚本。该脚本基于 ZCANPRO 提供的 zcanpro 内置库实现 CAN/CANFD
    报文收发，调度诊断会话、调用 DLL 解密，并实现底层的字节级解析算法。
  - VF65ZLGDll.dll：由算法开发或 ECU 供应商提供的加密库。用于根据种子（Seed）计算出对应的密钥（Key），以通过 ECU
    的安全认证安全访问级别（Security Access Level）。

2. 核心机制深度解析

2.1 UDS 诊断控制时序

脚本运行后，严格按照 ISO 14229 (UDS) 标准时序与目标 ECU 进行交互。具体流程如下：

1.  会话切换（Session Control）： 发送 10 03（切换至扩展诊断会话），使 ECU 允许执行敏感操作。
2.  安全访问（Security Access - Request Seed）： 发送 27 01，请求安全解锁种子。
3.  密钥计算（Key Calculation）： 脚本捕获 ECU 响应的 Seed，通过 ctypes 传递给 VF65ZLGDll.dll 计算出
    Key。
4.  安全校验（Security Access - Send Key）： 发送 27 02，将计算出的 Key 回传给 ECU。通过后，ECU
    释放高权限数据的读写限制。
5.  工作配置写入（Write Data By Identifier）： 发送 2E 02 33 01 55，向指定 DID
    写入状态配置参数（如触发锁定、清除标志等）。
6.  读取碰撞数据（Read Data By Identifier）： 依次发送 22 02 16、22 02 17、22 02 18、22 02 19
    读取原始的碰撞记录字节流。
7.  数据落盘： 每次读取成功后，立即调用 data_record() 进行解析并写入本地文件。

2.2 安全访问 DLL 接口规范

脚本通过 Python ctypes 模块调用 VF65ZLGDll.dll。DLL 中的核心函数 ZLGKey 接口原型及参数定义如下：

int ZLGKey(
    unsigned char* iSeedArray,       // 输入：Seed 字节数组指针
    unsigned short iSeedArraySize,   // 输入：Seed 字节数组长度
    unsigned int iSecurityLevel,     // 输入：安全等级 (Level 0x01 对应传入 1)
    const char* iVariant,            // 输入：变体名称字符串 (默认传入 "default")
    unsigned char* oKeyArray,        // 输出：计算生成的 Key 字节数组指针
    unsigned short* oKeyArraySize    // 输出/输入：Key 字节数组的实际大小指针
);

Python 绑定实现： 在 ReadCrashData.py 中，通过以下方式进行了参数类型绑定和调用：

dll.ZLGKey.argtypes = [
    ctypes.POINTER(ctypes.c_ubyte),
    ctypes.c_ushort,
    ctypes.c_uint,
    ctypes.c_char_p,
    ctypes.POINTER(ctypes.c_ubyte),
    ctypes.POINTER(ctypes.c_ushort)
]

2.3 碰撞数据解析引擎算法

解析引擎是本项目的核心难点，主要解决 ECU 内存中高密度的 C 语言联合体（Union）与结构体（Struct）转换为物理量的问题。

2.3.1 字节序还原机制（Endianness）

对于多字节字段（如 2 字节、4 字节变量），脚本采用混合大小端处理方式（根据目标芯片的存储架构）：

  - 1 字节（uint8/int8）：直接提取。
  - 2 字节（uint16/int16）：通过大端（Big-Endian）方式合并：raw = (data[start + 1],
    data[start])。
  - 4 字节（uint32/int32）：执行特殊的字内交叉大端合并： raw = (data[start + 1], data[start],
    data[start + 3], data[start + 2])。

2.3.2 动态路由逻辑（Dynamic Routing）

在读取 0x0218 和 0x0219 诊断标识符时，返回的数据可能属于左侧碰（LH），也可能属于右侧碰（RH）。

  - 脚本首先定位到数据中的 "event-type" 字段。
  - 若读取到该字段的值为 4，则证明当前碰撞记录为右侧碰撞，自动加载 SideCrashRH 的配置映射表进行解析。
  - 若值不为 4，则默认加载 SideCrashLH 进行解析。

2.3.3 未初始化 Flash 的过滤保护机制

在 ECU 运行中，如果某段 Flash 尚未写入碰撞记录，其物理介质上的表现为全亮状态（即全部字节均为 0xFF）。

  - 普通有符号字段：如果是 Signed: true 的变量（如 -1 补码表示为 0xFFFF），脚本将其作为有效负数正常解析。
  - 无符号字段与数组：如果是无符号数（如 SingleLength 的无符号标量或数组），一旦原始字节流全部为 0xFF，解析器会自动将其转换为
    Python 中的 None（在生成的 JSON 中表现为 null），避免产生无效的极大值干扰数据分析。

3. 配置文件映射规则说明

config_data_type.json 中定义的数据类型结构包括两种类型：

3.1 数组/复合类型定义

用于加速度波形（长度通常为 400 字节，包含 200 个 2 字节有符号整数点）：

"000_x_acc_variable": {
    "Length": 400,        // 字段在二进制流中占用的总字节数
    "SingleLength": 2,    // 数组中单个元素的字节数
    "Signed": true        // 元素是否是有符号数（用于区分正负加速度）
}

3.2 标量（单值）类型定义

用于状态、计数器或控制字：

  - 标准类型（整数定义）：表示该字段占用固定字节数。例如 "018_Fcoff": 4，代表占 4 字节的无符号整型变量。
  - 结构体总长标示：每个场景的最后，都包含一个 "algorithm_intermediate_variable"，定义了除 UDS
    帧头外的解析荷载总长度（例如 1856 字节），解析引擎会以此校验接收到的报文长度完整性。

4. ZCANPRO 软件对接与 API 说明

运行在 ZCANPRO 软件中的 Python 脚本需要遵循其特定的 API 规范。

  - zcanpro.get_buses()：获取 ZCANPRO 当前已打开的物理通道（支持 CAN、CANFD）。
  - zcanpro.uds_init(config)：初始化诊断协议栈。其中 enhanced_timeout_ms 能够自动应对 ECU
    忙碌时返回的消极响应悬挂状态（0x78 Pending）。
  - zcanpro.uds_request(busID, request)：执行单次诊断事务发送并等待响应。
  - zcanpro.write_log(message)：将调试和解析信息直接输出到 ZCANPRO 软件下方的控制台，便于测试人员在无 Python
    IDE 环境时进行排查。
  - z_notify(type, obj)：ZCANPRO 事件回调函数。当用户在软件界面点击“停止运行”时，软件会向脚本发送 "stop"
    通知，脚本捕获后会安全中止当前任务（stopTask = True）。

5. 详细部署与运行步骤

5.1 运行前置检查

1.  硬件连接：使用 ZLG 周立功 CAN 接口卡（如 USBCAN-E-U, USBCAN-II 等）连接电脑与目标 ECU 的 CAN
    物理总线，确认终端电阻（120 欧姆）匹配正常。
2.  动态库检查：
      - 检查 VF65ZLGDll.dll 属性。
      - 如果 ZCANPRO 是 64 位版本，必须使用 64 位的 DLL；如果是 32 位版本，必须使用 32 位的 DLL。

5.2 脚本加载与执行

1.  启动 ZCANPRO 软件，打开对应的 CAN 通道。
2.  在软件菜单栏中找到 “脚本” -> “执行脚本”（或自定义按钮）。
3.  选择本地的 ReadCrashData.py 脚本文件。
4.  点击 “运行”。
5.  观察 ZCANPRO 日志窗口，正常的输出时序应如下：
    [UDS Tx] 688  10 03
    [UDS Rx] 608  50 03 ...  Positive Response
    [UDS Tx] 688  27 01
    [UDS Rx] 608  67 01 AA BB CC DD ... (种子数据)
    [UDS Tx] 688  27 02 EE FF 11 22 ... (计算出的密钥)
    [UDS Rx] 608  67 02 ...  Positive Response
    ...
    [UDS Tx] 688  22 02 16
    [UDS Rx] 608  62 02 16 [大量数据字节...]
    000_x_acc_variable: [0, -1, -3, 2, ...]
    018_Fcoff: 4
    Data saved to .../data/0216-202X-XX-XX_XX_XX_XX.json
