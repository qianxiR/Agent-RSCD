# SAM3 权重（仅本机入库）

本目录保存 ProC-SAM3 所需的 SAM3 权重，**只提交到本地 Git，不推送 GitHub**。

## 文件

```text
sam3/sam3.pt                    3,450,062,241 bytes (3.45GB)
sam3/bpe_simple_vocab_16e6.txt.gz
sam3/config.json
sam3/configuration.json
sam3/merges.txt
sam3/LICENSE
```

SHA256（`sam3.pt`）：

```text
9999E2341CEEF5E136DAA386EECB55CB414446A00AC2B55EB2DFD2F7C3CF8C9E
```

## 来源

- 源目录：`E:\1代码\模型\权重\sam3`
- 复制日期：2026-11-01

## 配置

`backend/config.py` 默认：

```python
proc_sam3_weights_root = backend/model/ProC_SAM3/weights
```

即权重实际路径：

```text
backend/model/ProC_SAM3/weights/sam3/sam3.pt
```

可用 `PROC_SAM3_WEIGHTS_ROOT` 覆盖。

## 注意

- GitHub 普通 Git 单文件上限 100MB、LFS/Releases 单文件上限 2GB，本文件 3.45GB，**不能推送 GitHub**；
- 仅本机/内网使用时，保留本地提交即可；
- 若以后要迁移到 GitHub，请改用外部制品（OSS/S3/NAS/内网 HTTP）+ 下载校验脚本，不要直接推该文件。
