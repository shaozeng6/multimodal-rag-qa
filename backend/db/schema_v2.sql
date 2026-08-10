-- ============================================================================
-- schema_v2.sql  数据库规范化改造 DDL(Tier 1)
-- 说明: 手动导入。新表会自动被 create_all 兼容(已存在则跳过), 此脚本主要
--       用于: ① 显式建 4 张新表; ② 迁移 messages.metadata(JSON 垃圾桶)。
-- 建议顺序: 先备份 → 建新表 → 回填 → 删 metadata 列
-- ============================================================================
USE rag_enterprise;

-- ---------------------------------------------------------------------------
-- 1. 入库任务表(替代 ingestion/jobs.py 的 in-memory dict, 重启可查)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ingest_jobs (
    id               VARCHAR(12)  NOT NULL COMMENT 'job_id(uuid hex 前12位)',
    filename         VARCHAR(255) NOT NULL COMMENT '上传的原始文件名',
    user_id          INT          NULL     COMMENT '上传者(管理员)',
    status           ENUM('pending','running','success','error') NOT NULL DEFAULT 'pending',
    stage            VARCHAR(50)  NOT NULL DEFAULT '等待执行' COMMENT '当前阶段(OCR识别/分片/...)',
    documents_count  INT          NULL     COMMENT '入库条数',
    error            TEXT         NULL     COMMENT '失败原因',
    created_at       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_ingest_jobs_user (user_id),
    KEY idx_ingest_jobs_created (created_at),
    CONSTRAINT fk_ingest_jobs_user FOREIGN KEY (user_id)
        REFERENCES users (id) ON DELETE SET NULL
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT ='入库任务';

-- ---------------------------------------------------------------------------
-- 2. 知识文档元数据表(与 Milvus t_doc_collection 的 chunk 对应, 文档级记录)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS knowledge_documents (
    id            BIGINT        NOT NULL AUTO_INCREMENT,
    job_id        VARCHAR(12)   NULL     COMMENT '来源入库任务',
    filename      VARCHAR(255)  NOT NULL COMMENT '源文件名',
    filetype      VARCHAR(20)   NOT NULL DEFAULT 'pdf',
    title         VARCHAR(255)  NOT NULL DEFAULT '' COMMENT '文档标题(OCR 首标题)',
    status        ENUM('ingested','partial','failed','deleted') NOT NULL DEFAULT 'ingested',
    chunk_count   INT           NOT NULL DEFAULT 0  COMMENT '入库 chunk 数',
    image_count   INT           NOT NULL DEFAULT 0  COMMENT '其中图片条数',
    file_md5      VARCHAR(32)   NULL     COMMENT '源文件 MD5(去重)',
    uploaded_by   INT           NULL     COMMENT '上传者',
    created_at    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_doc_job (job_id),
    KEY idx_doc_md5 (file_md5),
    KEY idx_doc_uploader (uploaded_by),
    CONSTRAINT fk_doc_job FOREIGN KEY (job_id)
        REFERENCES ingest_jobs (id) ON DELETE SET NULL,
    CONSTRAINT fk_doc_uploader FOREIGN KEY (uploaded_by)
        REFERENCES users (id) ON DELETE SET NULL
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT ='知识库文档元数据';

-- ---------------------------------------------------------------------------
-- 3. 消息图片表(messages.metadata.images 拆出; 存引用不存 base64)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS message_images (
    id          BIGINT        NOT NULL AUTO_INCREMENT,
    message_id  BIGINT        NOT NULL,
    image_type  ENUM('input','retrieved','history') NOT NULL DEFAULT 'input'
                COMMENT 'input=用户输入图 / retrieved=检索命中图 / history=历史图',
    image_ref   VARCHAR(512)  NOT NULL COMMENT '图片引用(/uploads/xx.png 或 URL, 不存 base64)',
    caption     VARCHAR(500)  NULL     COMMENT '图片描述(可选, 取自 image_analysis caption)',
    created_at  DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_msg_img (message_id),
    CONSTRAINT fk_msg_img FOREIGN KEY (message_id)
        REFERENCES messages (id) ON DELETE CASCADE
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT ='消息图片';

-- ---------------------------------------------------------------------------
-- 4. 消息追踪表(messages.metadata.trace 拆出; 只写不回流, 供审计/调优)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS message_traces (
    id               BIGINT         NOT NULL AUTO_INCREMENT,
    message_id       BIGINT         NOT NULL COMMENT '关联 AI 消息(1:1)',
    session_id       VARCHAR(36)    NULL,
    input_text       VARCHAR(200)   NULL,
    modality         VARCHAR(20)    NULL COMMENT '输入模态 text/image/text_image',
    image_caption    VARCHAR(200)   NULL,
    image_relation   VARCHAR(20)    NULL COMMENT 'related/unrelated/contradictory',
    rewritten_query  VARCHAR(300)   NULL,
    sub_questions    JSON           NULL,
    kb_context       JSON           NULL COMMENT '检索命中摘要[{filename,category,score}]',
    kb_images        JSON           NULL COMMENT '检索命中的图片引用列表',
    retrieval_ok     TINYINT(1)     NULL,
    evaluate_score   FLOAT          NULL COMMENT 'LLM Judge 分(0~1)',
    human_answer     VARCHAR(10)    NULL COMMENT '人工审批 approve/reject',
    duration_ms      INT            NULL COMMENT '本轮耗时',
    created_at       DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_trace_msg (message_id),
    KEY idx_trace_session (session_id),
    CONSTRAINT fk_trace_msg FOREIGN KEY (message_id)
        REFERENCES messages (id) ON DELETE CASCADE
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT ='消息中间过程追踪';

-- 注: 2026-08-09 新增 modality 列(输入模态 text/image/text_image)。
-- 已有库的 message_traces 不会由 create_all 自动加列, 需手动执行:
--   ALTER TABLE message_traces ADD COLUMN modality VARCHAR(20) NULL
--       COMMENT '输入模态 text/image/text_image' AFTER input_text;

-- ---------------------------------------------------------------------------
-- 5. 存量回填 + 删除 messages.metadata(确认无误后再执行)
--    metadata 结构: human -> {"images": ["data:..."]}; ai -> {"images": [...], "trace": {...}}
--    回填仅覆盖首张图片 + trace 常见字段; 若数据不重要可跳过回填直接删列。
-- ---------------------------------------------------------------------------
-- INSERT INTO message_images (message_id, image_type, image_ref, created_at)
-- SELECT m.id, 'input', JSON_UNQUOTE(JSON_EXTRACT(m.metadata, '$.images[0]')), m.created_at
-- FROM messages m
-- WHERE JSON_LENGTH(JSON_EXTRACT(m.metadata, '$.images')) > 0;

-- INSERT INTO message_traces (message_id, session_id, input_text, image_caption,
--                             image_relation, rewritten_query, sub_questions,
--                             kb_context, kb_images, retrieval_ok, evaluate_score,
--                             human_answer, duration_ms, created_at)
-- SELECT m.id, m.session_id,
--        JSON_UNQUOTE(JSON_EXTRACT(m.metadata, '$.trace.input_text')),
--        JSON_UNQUOTE(JSON_EXTRACT(m.metadata, '$.trace.image_caption')),
--        JSON_UNQUOTE(JSON_EXTRACT(m.metadata, '$.trace.image_relation')),
--        JSON_UNQUOTE(JSON_EXTRACT(m.metadata, '$.trace.rewritten_query')),
--        JSON_EXTRACT(m.metadata, '$.trace.sub_questions'),
--        JSON_EXTRACT(m.metadata, '$.trace.kb_context'),
--        JSON_EXTRACT(m.metadata, '$.trace.kb_images'),
--        CAST(JSON_EXTRACT(m.metadata, '$.trace.retrieval_ok') AS UNSIGNED),
--        CAST(JSON_EXTRACT(m.metadata, '$.trace.evaluate_score') AS DECIMAL(3,2)),
--        JSON_UNQUOTE(JSON_EXTRACT(m.metadata, '$.trace.human_answer')),
--        CAST(JSON_EXTRACT(m.metadata, '$.trace.duration_ms') AS UNSIGNED),
--        m.created_at
-- FROM messages m
-- WHERE JSON_EXTRACT(m.metadata, '$.trace') IS NOT NULL;

-- ALTER TABLE messages DROP COLUMN metadata;
