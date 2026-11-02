# ProC-SAM3 vendored code

本目录是 ProC-SAM3 的**运行时代码副本**，不包含权重和数据。

## 来源

- 本地源目录：`E:\1代码\模型\变化检测\ProC-SAM3-main`
- 复制日期：2026-11-01
- 源目录不是 git 仓库，无 commit 可记录

## 包含

```text
sam3_segmentor_cached.py      推理主类（系统实际使用）
sam3_segmentor.py             基础 segmentor
config_qwen3_sam3.py          Qwen3-SAM3 配置
mask_postprocess.py           掩码后处理
palettes.py                   调色板
configs/                      模型/类别配置
core/                         PromptBank、GroundingExecutionEngine 等
sam3/                         SAM3 模型代码、配置、tokenizer 词表
mmseg/                        分割框架代码
mmengine/                     训练/配置/注册框架代码
_vendor/mmcv/                 mmcv 兼容层代码
```

## 不包含

- 模型权重（`sam3.pt` 等）
- 测试数据（`QwSAM3TestData/`）
- 演示输出（`demo_out/`）
- 文档图片（`assets/`）
- GUI、demo、eval、训练脚本、`__pycache__`
- 训练配置/测试资源（除运行所需的 yaml/txt/gz）

## 配置

- 代码路径：默认 `backend/model/ProC_SAM3/vendor`，可用 `PROC_SAM3_ROOT` 覆盖；
- 权重路径：默认 `backend/model/ProC_SAM3/weights`，可用 `PROC_SAM3_WEIGHTS_ROOT` 覆盖；
- 权重文件：`<PROC_SAM3_WEIGHTS_ROOT>/sam3/sam3.pt`，本机已复制（仅本地提交，不推 GitHub）。

## 修改

仅复制文件，未改业务代码；`engine.py` 负责把 vendor 根目录和 `_vendor` 加入 `sys.path`。
