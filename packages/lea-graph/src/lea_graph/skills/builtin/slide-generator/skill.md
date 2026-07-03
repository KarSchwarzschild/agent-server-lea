---
name: slide-generator
display_name: 演示文稿生成
description: 根据用户主题生成高质量 SVG 演示文稿，支持多种布局模板
category: document
trigger_keywords: ["PPT", "ppt", "演示文稿", "幻灯片", "slides", "presentation", "做个PPT", "做PPT"]
model_tier: high
context: fork
execution_mode: pipeline
pipeline_class: lea_graph.skills.pipelines.slide_generator.SlideGeneratorPipeline
allowed_tools: []
---
你是一位专业的演示文稿设计专家，擅长将用户提供的主题内容转化为高质量的可视化演示文稿。
