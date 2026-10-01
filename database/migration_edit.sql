-- ============================================================
-- 模块 1：编辑帖子与回复功能 数据库迁移脚本
-- 为 posts / replies 表添加 updated_at 字段（最后编辑时间）
--
-- 适用：MariaDB（与本仓库 migration_v2/v3.sql 保持一致，使用 IF NOT EXISTS）
-- 注意：MySQL 8.x 不支持 ADD COLUMN IF NOT EXISTS，
--       如需在 MySQL 上执行，请删除该子句后再运行。
-- 幂等：本脚本可重复执行。
-- ============================================================

USE `forum_db`;

-- ------------------------------------------------------------
-- 1. posts 表添加"最后编辑时间"字段
--    NULL 表示帖子从未被编辑
-- ------------------------------------------------------------
ALTER TABLE `posts`
    ADD COLUMN IF NOT EXISTS `updated_at` DATETIME DEFAULT NULL
    COMMENT '最后编辑时间（模块 1），NULL 表示从未编辑' AFTER `created_at`;

-- ------------------------------------------------------------
-- 2. replies 表添加"最后编辑时间"字段
--    NULL 表示回复从未被编辑
-- ------------------------------------------------------------
ALTER TABLE `replies`
    ADD COLUMN IF NOT EXISTS `updated_at` DATETIME DEFAULT NULL
    COMMENT '最后编辑时间（模块 1），NULL 表示从未编辑' AFTER `created_at`;

-- ------------------------------------------------------------
-- 验证新字段
-- ------------------------------------------------------------
-- DESCRIBE posts;
-- DESCRIBE replies;
