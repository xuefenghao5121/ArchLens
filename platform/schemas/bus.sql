-- 层间数据总线：5 种标准类型 + 层组件签名 + 类型兼容矩阵
-- 所有层包的 payload.output_type 必须是这 5 种之一
-- 场景模板从层组件注册表中选择组件并定义类型流

CREATE TABLE layer_types (
    type_name TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    required_fields TEXT NOT NULL,  -- JSON 数组
    optional_fields TEXT NOT NULL   -- JSON 数组
);

INSERT INTO layer_types VALUES
('ProblemStatement', '问题陈述——L1/L2 产出，下游可消费', 
 '["problem_id","description","metrics","expected_outcome"]',
 '["hypothesis","root_cause","trigger_condition","evidence_refs","attribution_level"]'),
('SolutionSpec', '方案规范——L3/L4 产出',
 '["solution_id","problem_ref","approach","acceptance_criteria"]',
 '["equivalence_spec","resource_requirements"]'),
('VerifiedArtifact', '已验证工件——L5/L6 产出',
 '["artifact_ref","verification_method","verification_result","scope"]',
 '["solution_ref","guards","auto_solution_spec"]'),
('ExperimentResult', '实验结果——L7 产出',
 '["experiment_id","artifact_refs","measurements","criteria_evaluation"]',
 '["reproduce_command","guard_status"]'),
('Conclusion', '结论——L8 产出',
 '["conclusion_id","status","boundary"]',
 '["evidence_chain","unverified_assumptions"]');

CREATE TABLE layer_components (
    layer_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    input_types TEXT NOT NULL,     -- JSON 数组：可接受的输入类型
    output_type TEXT NOT NULL,
    enriches TEXT,                  -- 增强哪些字段（null = 转换型）
    requires_from_input TEXT,      -- 输入必须已含的字段（null = 无）
    human_weight TEXT NOT NULL,    -- high / medium / low
    requires_test_evidence INTEGER NOT NULL
);

INSERT INTO layer_components VALUES
('L1', 'observation', '[]', 'ProblemStatement',
 '["description","trigger_condition","metrics","expected_outcome"]', NULL, 'high', 0),
('L2', 'attribution', '["ProblemStatement"]', 'ProblemStatement',
 '["hypothesis","root_cause","trigger_condition"]', NULL, 'high', 1),
('L3', 'solution', '["ProblemStatement"]', 'SolutionSpec',
 NULL, '["root_cause"]', 'high', 0),
('L4', 'contract', '["SolutionSpec"]', 'SolutionSpec',
 '["equivalence_spec"]', NULL, 'high', 0),
('L5', 'rtl_implementation', '["SolutionSpec","ProblemStatement"]', 'VerifiedArtifact',
 NULL, NULL, 'medium', 1),
('L5d', 'rtl_direct_fix', '["ProblemStatement"]', 'VerifiedArtifact',
 NULL, NULL, 'medium', 1),
('L6', 'integration', '["VerifiedArtifact"]', 'VerifiedArtifact',
 '["scope"]', NULL, 'high', 1),
('L7', 'experiment', '["VerifiedArtifact"]', 'ExperimentResult',
 NULL, '["verification_result"]', 'low', 1),
('L8', 'conclusion', '["ExperimentResult","VerifiedArtifact"]', 'Conclusion',
 NULL, NULL, 'high', 1);

-- 类型兼容矩阵：from_type 能否作为 to_layer 的输入
CREATE TABLE type_compatibility (
    from_type TEXT NOT NULL,
    to_layer TEXT NOT NULL,
    compatible INTEGER NOT NULL,
    PRIMARY KEY (from_type, to_layer)
);

INSERT INTO type_compatibility VALUES
-- ProblemStatement 可直达 L2/L3/L5/L5d
('ProblemStatement', 'L2', 1), ('ProblemStatement', 'L3', 1),
('ProblemStatement', 'L5', 1),  ('ProblemStatement', 'L5d', 1),
('ProblemStatement', 'L4', 0), ('ProblemStatement', 'L6', 0),
('ProblemStatement', 'L7', 0),  ('ProblemStatement', 'L8', 0),
-- SolutionSpec 可达 L4/L5
('SolutionSpec', 'L4', 1), ('SolutionSpec', 'L5', 1),
('SolutionSpec', 'L2', 0), ('SolutionSpec', 'L3', 0),
('SolutionSpec', 'L5d', 0), ('SolutionSpec', 'L6', 0),
-- VerifiedArtifact 可达 L6/L7/L8
('VerifiedArtifact', 'L6', 1), ('VerifiedArtifact', 'L7', 1),
('VerifiedArtifact', 'L8', 1),
('VerifiedArtifact', 'L2', 0), ('VerifiedArtifact', 'L3', 0),
-- ExperimentResult 可达 L8
('ExperimentResult', 'L8', 1),
('ExperimentResult', 'L2', 0), ('ExperimentResult', 'L3', 0),
('ExperimentResult', 'L5', 0), ('ExperimentResult', 'L6', 0),
-- Conclusion 不可达任何层（终点）
('Conclusion', 'L1', 0), ('Conclusion', 'L2', 0), ('Conclusion', 'L8', 0);

-- 归因强度分级
CREATE TABLE attribution_levels (
    level INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    required_fields TEXT NOT NULL,
    template_recommendation TEXT NOT NULL
);

INSERT INTO attribution_levels VALUES
(0, 'symptom_only', 'issue 只描述症状不解释原因', '["description"]', 'bug_fix_with_attribution'),
(1, 'root_cause_identified', 'issue 或初步分析已定位根因', '["description","root_cause"]', 'bug_fix_simple'),
(2, 'mechanism_understood', '理解了根因的机制（为什么错）', '["description","root_cause","hypothesis"]', 'bug_fix_simple'),
(3, 'alternative_excluded', '排除了替代解释', '["description","root_cause","hypothesis","evidence_refs"]', 'microarch_optimize');

-- 场景模板
CREATE TABLE scenario_templates (
    template_name TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    nodes TEXT NOT NULL,           -- JSON: [{component, alias}]
    edges TEXT NOT NULL,           -- JSON: [{from_alias, to_alias}]
    gate_overrides TEXT NOT NULL   -- JSON: {alias: {required_fields: [...]}}
);

INSERT INTO scenario_templates VALUES
('bug_fix_simple',
 'issue 已含根因（归因 Level ≥1），直达修复+验证',
 '[{"component":"L1","alias":"anchor"},{"component":"L5d","alias":"fix"},{"component":"L8","alias":"conclude"}]',
 '[{"from_alias":"anchor","to_alias":"fix"},{"from_alias":"fix","to_alias":"conclude"}]',
 '{"fix":{"required_fields":["root_cause","trigger_condition","fix_location","stimulus_design"]}}'),

('bug_fix_with_attribution',
 'issue 只有症状（归因 Level 0），需轻量归因后再修',
 '[{"component":"L1","alias":"anchor"},{"component":"L2","alias":"attribute"},{"component":"L5d","alias":"fix"},{"component":"L8","alias":"conclude"}]',
 '[{"from_alias":"anchor","to_alias":"attribute"},{"from_alias":"attribute","to_alias":"fix"},{"from_alias":"fix","to_alias":"conclude"}]',
 '{"fix":{"required_fields":["root_cause","trigger_condition","fix_location","stimulus_design"]}}'),

('microarch_optimize',
 '完整八层——微架构优化任务（FLOW-1 形态）',
 '[{"component":"L1","alias":"l1"},{"component":"L2","alias":"l2"},{"component":"L3","alias":"l3"},{"component":"L4","alias":"l4"},{"component":"L5","alias":"l5"},{"component":"L6","alias":"l6"},{"component":"L7","alias":"l7"},{"component":"L8","alias":"l8"}]',
 '[{"from_alias":"l1","to_alias":"l2"},{"from_alias":"l2","to_alias":"l3"},{"from_alias":"l3","to_alias":"l4"},{"from_alias":"l4","to_alias":"l5"},{"from_alias":"l5","to_alias":"l6"},{"from_alias":"l6","to_alias":"l7"},{"from_alias":"l7","to_alias":"l8"}]',
 '{}'),

('batch_calibration',
 '批量标定——共享 L1 + 每例 L5d + 共享 L8 汇总',
 '[{"component":"L1","alias":"shared_anchor"},{"component":"L5d","alias":"case_N","repeat":"per_case"},{"component":"L8","alias":"aggregate"}]',
 '[{"from_alias":"shared_anchor","to_alias":"case_N"},{"from_alias":"case_N","to_alias":"aggregate"}]',
 '{"case_N":{"required_fields":["root_cause","trigger_condition","fix_location","stimulus_design"]}}'),

('comparative_study',
 '对比研究——跳过设计层，只做归因+测量+分析',
 '[{"component":"L1","alias":"anchor"},{"component":"L2","alias":"attribute"},{"component":"L7","alias":"measure"},{"component":"L8","alias":"conclude"}]',
 '[{"from_alias":"anchor","to_alias":"attribute"},{"from_alias":"attribute","to_alias":"measure"},{"from_alias":"measure","to_alias":"conclude"}]',
 '{}'),

('component_design',
 '新组件设计——跳过 L1/L2（问题已定义）',
 '[{"component":"L3","alias":"design"},{"component":"L4","alias":"contract"},{"component":"L5","alias":"implement"},{"component":"L6","alias":"integrate"},{"component":"L7","alias":"verify"}]',
 '[{"from_alias":"design","to_alias":"contract"},{"from_alias":"contract","to_alias":"implement"},{"from_alias":"implement","to_alias":"integrate"},{"from_alias":"integrate","to_alias":"verify"}]',
 '{}');
