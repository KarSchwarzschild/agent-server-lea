---
name: slide-editor
display_name: 演示文稿编辑
description: 对已生成的演示文稿进行对话式编辑，支持修改、新增、删除页面
category: document
trigger_keywords: ["修改PPT", "编辑幻灯片", "改一下PPT"]
model_tier: high
context: fork
execution_mode: pipeline
pipeline_class: lea_graph.skills.pipelines.slide_editor.SlideEditorPipeline
allowed_tools: []
---
你是演示文稿编辑助手，根据用户的自然语言指令修改已有的演示文稿内容。
