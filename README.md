# GPON 巡检工具 (GPON Inspection Tool)

一个基于 **PyQt5** 的图形化 GPON 网络巡检工具，用于解析 OLT 设备导出的诊断包 `diag.tar.gz`，自动完成多项健康检查，并生成可交互的 HTML 检测报告。

---

## 功能特性

工具会从 `diag.tar.gz` 中读取以下关键文件：

| 文件名 | 用途 |
|--------|------|
| `log_system_info.txt` | 系统信息（时间、CPU、内存、VLAN、温度、版本、IP、ONU 状态、VLAN 转换等） |
| `ontInfoDetail.txt` | ONT 详细信息 |
| `ontTransceiver.txt` | ONT 光模块（光功率）信息 |
| `oltCounter.txt` | OLT 计数器（CRC 错包） |
| `onuCounter.txt` | ONU 计数器 |

### 内置检查项

| # | 检查项 | 说明 |
|---|--------|------|
| 1 | CRC 错包检测 | 检测 PON 口 `rxCrcErrors` 是否非 0 |
| 2 | 版本问题检测 | 主控/线卡版本一致性、版本过老、近期重启 |
| 3 | 时间同步检测 | OLT 本地时间是否过早（未正确设置） |
| 4 | CPU 和内存使用率检测 | 主用/备用主控 CPU ≥90%、内存 >80% 告警 |
| 5 | VLAN 使用检测 | 是否使用 1~24 保留 VLAN |
| 6 | 温度检测 | CSM >110°C、Slot >90°C 告警 |
| 7 | ONT 光功率检测 | Rx power 是否超出 -15 ~ -23 dBm 正常范围 |
| 8 | 带外地址 IP 禁止 | Management IP 是否落入 192.168.100.0/24 或 172.31.0.0/16 |
| 9 | 带内地址 IP 禁止 | 默认路由网关是否落入禁止网段 |
| 10 | ONU 状态检测 | Authenticated / Unauthenticated ONU 状态分类告警 |
| 11 | VLAN 转换与 Flow/TCONT 一致性检测 | VLAN Translate、Flow、TCONT、ONU virtual-port、Service 四方一致性校验 |

---

## 环境要求

- Python 3.7+
- PyQt5

## 安装依赖

```bash
pip install PyQt5
