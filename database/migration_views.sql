-- ============================================================
-- 模块 3：浏览量统计功能 数据库迁移脚本
-- 为 posts 表添加 view_count 字段（帖子浏览次数）
--
-- 适用：MariaDB（与本仓库 migration_v2/v3/edit.sql 保持一致，使用 IF NOT EXISTS）
-- 注意：MySQL 8.x 不支持 ADD COLUMN IF NOT EXISTS，
--       如需在 MySQL 上执行，请删除该子句后再运行。
-- 幂等：本脚本可重复执行；存量行自动填充默认值 0。
-- ============================================================

USE `forum_db`;

-- ------------------------------------------------------------
-- posts 表添加"浏览次数"字段
--    新帖与存量帖默认 0；应用层在 GET /api/posts/{id} 中
--    使用原子语句 UPDATE ... SET view_count = view_count + 1 维护。
-- ------------------------------------------------------------
ALTER TABLE `posts`
    ADD COLUMN IF NOT EXISTS `view_count` INT NOT NULL DEFAULT 0
    COMMENT '帖子浏览次数统计（模块 3），30 分钟内同一用户/IP 只计一次' AFTER `updated_at`;

-- ------------------------------------------------------------
-- 验证新字段
-- ------------------------------------------------------------
-- DESCRIBE posts;
-- SELECT id, title, view_count FROM posts ORDER BY view_count DESC LIMIT 10;
