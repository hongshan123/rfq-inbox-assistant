# RFQ Demo 本地预处理

当前应用启动和模型配置见 [README.md](README.md)。本机已安装真实 Tesseract 与 eng/ara，六页已补齐 OCR 并保留人工复核标记；详情见 `data/processed/rfq-demo/ocr_report.md`。

环境为 `.venv-rfq-data`，依赖版本固定在 `requirements-rfq-data.txt`。脚本使用本地 pypdf、openpyxl 和 jsonschema；没有云 OCR 或大模型请求，也不运行发布方仓库的生成代码。

在项目根目录的 PowerShell 执行：

```powershell
python -m venv .venv-rfq-data
& '.\.venv-rfq-data\Scripts\python.exe' -m pip install -r requirements-rfq-data.txt
& '.\.venv-rfq-data\Scripts\python.exe' scripts/prepare_rfq_data.py --raw-dir data/raw/rfq-demo --out-dir data/processed/rfq-demo --ocr auto
& '.\.venv-rfq-data\Scripts\python.exe' scripts/complete_ocr.py
& '.\.venv-rfq-data\Scripts\python.exe' scripts/validate_rfq_data.py --data-dir data/processed/rfq-demo
```

`--ocr off` 保留需要 OCR 的页面与原因，不把空文本算成功。`--ocr auto` 会检查本地 Tesseract、Python 渲染依赖和语言包。可用 `TESSERACT_CMD` 指向本地引擎。阿拉伯文与混合语言需要 `ara`、`eng`，未知图像语言也按这两种语言保守检查。每个待 OCR PDF 页面分别以 300 DPI 渲染，不能用第一页 PNG 替代后续页。OCR 文本未经人工校对；`ok` 仅表示提取到了非空且未触发质量启发式的文本，不是业务字段准确率。

本机已安装 Tesseract，语言包位于 `tools/tessdata/`；来源记录见 `tools/ocr-installation.json` 与语言包 manifest。增量补齐执行 `scripts/complete_ocr.py`，不读取标准答案。Python 自带 OpenSSL 与 PyPI 的 TLS 连接不兼容时，安装阶段用系统 curl 从官方 PyPI 获取兼容 wheel、核对 PyPI 提供的 SHA-256，再用 pip 离线安装，没有关闭证书检查。这只下载依赖，未上传任何文档。

输出：

- `inputs.jsonl`：一个逻辑文档一行，PDF 与同场景 PNG 共用 document_id。source_files 是相对 raw-dir 的路径，仅包含实际输入；不含答案或 fixture README。
- `gold.jsonl`：发布方原始 JSON 结构、原始 JSON 文本、schema 校验结果；邮件 YAML 标签只会在这里保存并标记未经人工确认。
- `text/`：按稳定 document_id 保存 JSON 证据和文本预览。来源位置使用从 1 开始的 PDF 页码或 Excel 工作表名与单元格坐标。
- `evaluation/schemas/`：发布方 schema 的原字节副本。
- `demo-emails/`：两封本地 MIME 邮件，附件字节与源 PDF 相同。历史 RFQ 附件即使部分页待 OCR，也可作为原始 PDF 附件上传测试。
- `manifest.json`、`validation.json`、`processing_report.md`：来源、许可、哈希、配对、实际状态与独立检查。验证通过代表数据结构与记录吻合，待 OCR 数量仍然单独报告。

Excel 以公式视图与缓存视图分别打开。缓存缺失保留 null，不重新计算或保存原工作簿。空白、占位符与模板零值仅作为模板内容保存。

可选 Parquet 缺失时跳过；应用已包含 Streamlit 所需的 pyarrow，但不因此生成额外邮件数据。存在 `extraction_yaml` 时再安装 PyYAML。整条 `input` 保持原语言、按完整线程保存，后续答复不能作为初始询价已知事实。标签使用 `yaml.safe_load`，不进入可靠准确率评估。

重复运行重建 JSONL，不追加记录。document_id 来自稳定逻辑路径。输出索引 `.rfq-generated.json` 只登记本工具生成文件；不会覆盖未登记的用户文件，过期文件只按该索引逐个删除。原始目录在处理前后逐文件比对哈希。

回归检查（使用临时副本，不修改原始数据）：

```powershell
& '.\.venv-rfq-data\Scripts\python.exe' -m unittest discover -s tests -p test_rfq_data.py -v
```

检查包括重复执行、标签扰动不影响输入、坏 PDF 不阻断 Excel、前导零/单位字符与缺失公式缓存保留、真正渲染多页 PDF、缺少语言包保持待 OCR，以及验证器拒绝答案注入和丢失页面。
