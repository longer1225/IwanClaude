---
name: slide-deck
description: 生成演示 PPT（pptx）：结构化大纲 → 版式装配 → 溢出自检，一次产出可上台的片子
invocation: both
icon: 📽️
keywords:
  - PPT
  - 做ppt
  - 幻灯片
  - 演讲稿
  - slides
  - deck
  - 汇报片子
allowed_tools:
  - read_file
  - list_dir
  - bash
  - run_python
  - write_file
  - pip_manage
---

帮我做一份演示文稿，主题与素材如下：

$ARGUMENTS

## 流程（按序执行，不许跳步）

1. **先出大纲再动手**：把素材收敛成 6–12 页的骨架（标题页 / 议程 / 内容页 / 结论页），
   每页一句话主张 + ≤3 个支撑点，先给用户过目；用户没异议再生成文件。
2. **依赖自检**：`python -c "import pptx"` 失败就用 pip_manage 装 `python-pptx`（一次即可）。
3. **写脚本生成**：把装配脚本 `build_deck.py` 写入会话工作目录再 run_python 执行，
   输出 `<主题>.pptx`。不要逐页手工调，一切版式进代码，改口径只改参数。
4. **交付前自检**（必做，脚本末尾追加校验段）：重新打开生成的 pptx，统计页数、
   每页文本框数，按下节"字数预算"粗查溢出；打印自检结果再回复用户。

## 版式规范（硬约束）

- 16:9（`prs.slide_width=Inches(13.333)`、`slide_height=Inches(7.5)`）
- 中文字体**微软雅黑**，且必须同时设 eastAsia 元素（见骨架），否则 PowerPoint 回退宋体
- 字号阶梯：页标题 30–32pt 粗体；正文 16–20pt；注脚 ≥12pt。一页最多 6 行正文
- 配色：主色 1 + 中性灰阶 + 语义红/绿各 1（例：#1A66FF / #1F2329 / #8A9099 / #E5484D / #30A46C），
  全片禁止第四种彩色
- 每页一个主张：标题写成结论句（"召回率两周提升 34%"），不写名词短语（"召回率数据"）

## 字数预算（防溢出口算）

- 标题区宽 11"：每行约 38 个汉字（16pt 起算，字号每大 2pt 行容量 -12%）
- 正文每页 ≤ 180 汉字；超了就拆页，**禁止缩小字号硬塞**
- 表格：列宽显式分配（`table.columns[i].width`），长文本列 >50% 表宽即视为版式错误

## 已知坑（python-pptx）

- 中文粗体只对 latin 生效，eastAsia 需在 `rPr` 上补 `a:ea` 的 typeface
- 文本框不会自动缩字：`word_wrap=True` 只保证换行，超框照样裁切，预算必须自己做
- 表格默认样式带蓝色横纹，用 `tbl.first_row=False` 类开关或干脆改 XML 才干净
- 模板占位符（`prs.slides[0]` 之类）在空白版式下为 None，统一用
  `slide_layouts[6]`（blank）+ 手工 add_textbox，行为最可预测

## 最小骨架

```python
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.oxml.ns import qn

PRIMARY, INK, MUTE = RGBColor(0x1A, 0x66, 0xFF), RGBColor(0x1F, 0x23, 0x29), RGBColor(0x8A, 0x90, 0x99)

def _font(run, size=18, bold=False, color=INK):
    f = run.font
    f.size, f.bold, f.name, f.color.rgb = Pt(size), bold, "Microsoft YaHei", color
    rPr = run._r.get_or_add_rPr()          # 中文字体必须补 eastAsia，否则回退宋体
    ea = rPr.find(qn("a:ea"))
    if ea is None:
        ea = rPr.makeelement(qn("a:ea"), {}); rPr.append(ea)
    ea.set("typeface", "微软雅黑")

def add_text(slide, x, y, w, h, lines, size=18, bold=False, color=INK):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame; tf.word_wrap = True
    for i, ln in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        run = p.add_run(); run.text = ln; _font(run, size, bold, color)
```

内容页一律 `prs.slide_layouts[6]` 起页，标题放 (0.7, 0.5, 11.9, 0.9)、正文放 (0.7, 1.6, 11.9, 5.2)。
