# 第三方许可证索引

本目录收录打包进 `蛆王争霸赛.exe` 的第三方依赖的许可证原文，逐字复制自本机安装包的 dist-info，未作改动。

| 文件 | 组件 | 版本 | 许可证 | 用途 |
| --- | --- | --- | --- | --- |
| `pywebview-6.2.1-BSD-3-Clause.txt` | pywebview | 6.2.1 | BSD 3-Clause | 桌面窗口 + WebView2 |
| `pystray-0.19.5-GPLv3.txt` | pystray | 0.19.5 | GPLv3 基础文本 | LGPLv3 引用 GPLv3 作为基础，故一并附带 |
| `pystray-0.19.5-LGPLv3.txt` | pystray | 0.19.5 | LGPLv3 附加条款 | pystray 实际以 LGPLv3 分发，两份合起来构成完整协议 |
| `Pillow-13.0.0-MIT-CMU.txt` | Pillow | 13.0.0 | MIT-CMU / HPND | 图像处理 |
| `PyInstaller-6.22.3-GPLv2-exception.txt` | PyInstaller | 6.22.3 | GPLv2 + 打包例外 | 打包工具，例外条款允许打包并分发闭源程序 |

## 分发提醒

- BSD-3 / MIT-CMU / PyInstaller 例外：保留版权声明与许可证文本即可。
- **pystray（LGPLv3）**：
  - LGPL 属弱 copyleft，**允许闭源 / 专有程序调用**，不会传染本项目的记分台代码。义务只在"库本身"上。
  - 本项目**未修改 pystray 源码**，按上游原样打包，因此**无需公开任何己方源码**。
  - LGPL 要求用户有权替换该库。本仓库已随附全部源码与 PyInstaller 打包命令，用户可自行替换 pystray 后重新打包，满足替换权的精神。
  - 若日后改动了 pystray 本体，那部分改动需按 LGPL 公开。
  - 重新分发 exe 时请保留本目录全部文本。

## 结论

上述依赖中**没有一个是强 copyleft（GPL）直接授权**，PyInstaller 的 GPL 带打包例外、pystray 是 LGPL 弱 copyleft，均不与"闭源分发 exe"冲突。只要保留本目录许可证文本、不修改库源码，即无侵权风险。
