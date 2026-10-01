---
name: word-report
description: 生成 Word 文档（docx）：方案/报告/总结类长文，标题层级+表格+页眉页脚，一次产出可交付的成稿
invocation: both
icon: 📄
keywords:
  - word
  - 文档报告
  - 写报告
  - docx
  - 生成文档
  - 方案文档
allowed_tools:
  - read_file
  - list_dir
  - bash
  - run_python
  - write_file
  - pip_manage
---

帮我写一份 Word 文档，主题与素材如下：

$ARGUMENTS

## 流程（按序执行）

1. **先列章节树**（H1 标题 + 两级目录 + 每节一句话概要）给用户确认方向；
   短材料（<500 字）可跳过这步直接成稿。
2. **依赖自检**：`python -c "import docx"` 失败就 pip_manage 装 `python-docx`。
3. **脚本生成** `build_doc.py` → run_python → `<标题>.docx`。
4. **自检**：重开文件统计段落数/表格数/字数，检查无空章节（有标题没正文即失败），
   打印结果后再交付。

## 版式规范（硬约束）

- A4 纵向，页边距上下 2.54cm、左右 3.17cm（python-docx 默认 section 即是，别乱改）
- 正文**中文宋体 / 西文 Times New Roman** 10.5pt（五号）、1.5 倍行距、首行缩进 2 字符；
  技术文档可去缩进改段后距 6pt，二选一全篇一致
- 标题：H1 三号黑体居中、H2 四号黑体、H3 小四黑体；**黑体也必须设 eastAsia**（同下骨架）
- 表格用 `Table Grid` 样式起步，表头加粗+底纹（shading 灰 15%），数字列右对齐
- 全文超过 3 页要加页眉（文档标题）与页码（页脚居中，"第 X 页"域代码）

## 已知坑（python-docx）

- `style.font.name` 只管西文；中文必须 `style._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")`，
  Heading 系列样式要逐个样式补，否则标题回退
- 页码没有 API，只能往 footer 塞 `PAGE` 域的 OXML（见骨架），忘了就是"无页码"交付
- 单元格宽度要先 `table.autofit=False` 再逐列 `cells[i].width`，且**每个单元格都要设**
  （Word 以 cell 为准不以 column 为准）
- `add_page_break` 挂在 run 上：`p.add_run().add_break(WD_BREAK.PAGE)`，不存在
  `document.add_page_break()` 这种顶层方法（有但只加空段，控制不了样式）

## 最小骨架

```python
from docx import Document
from docx.shared import Pt, RGBColor
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from docx.enum.text import WD_ALIGN_PARAGRAPH

def _east_asia(obj_style_or_run, east="宋体", latin=None):
    """中西文字体分开设置——python-docx 的 name 只作用西文，这是第一大坑"""
    font = obj_style_or_run.font
    if latin:
        font.name = latin
    rpr = obj_style_or_run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts"); rpr.append(rfonts)
    rfonts.set(qn("w:eastAsia"), east)

doc = Document()
normal = doc.styles["Normal"]
normal.font.size = Pt(10.5)
_east_asia(normal, "宋体", "Times New Roman")
for h in ("Heading 1", "Heading 2", "Heading 3"):
    _east_asia(doc.styles[h], "黑体")

def add_page_number(doc, section):
    """页码域：footer 段落里塞 fldChar begin / instrText PAGE / fldChar end"""
    p = section.footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run()
    for el, attr, text in (("begin", "w:fldCharType", None),
                           (None, None, "PAGE"),
                           ("end", "w:fldCharType", None)):
        if el:
            f = OxmlElement("w:fldChar"); f.set(qn(attr), el); r._r.append(f)
        else:
            t = OxmlElement("w:instrText"); t.text = text; r._r.append(t)
add_page_number(doc, doc.sections[0])
```

正文一律 `doc.add_paragraph()` + 手工 `_east_asia(run)` 兜底；素材给了数据就先成表，
别把数字散在段落里。
