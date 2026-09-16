-- 021 · Skill 分类回填并设为必填
--
-- 旧数据都沿用了 general 默认值，按 Skill 的实际用途回填；之后分类由创建者明确提供。
UPDATE skills
SET category = CASE
    WHEN name IN ('pptx', 'pptx-generator', 'ppt_generator', 'pdf') THEN 'file'
    WHEN name IN ('xlsx', 'alice_weekly_report') THEN 'data'
    WHEN name = 'internal-comms' THEN 'writing'
    WHEN name = 'skill-creator' THEN 'development'
    ELSE category
END
WHERE name IN (
    'pptx', 'pptx-generator', 'ppt_generator', 'pdf', 'xlsx',
    'alice_weekly_report', 'internal-comms', 'skill-creator'
);

ALTER TABLE skills MODIFY COLUMN category VARCHAR(50) NOT NULL;
