-- ============================================================
-- V2.0 → V3.0 数据库迁移脚本
-- 为已存在的数据库添加回复层级（楼中楼）字段
--
-- 适用：MariaDB（与本仓库 migration_v2.sql 保持一致，使用 IF NOT EXISTS）
-- 注意：MySQL 8.x 不支持 ADD COLUMN IF NOT EXISTS，
--       如需在 MySQL 上执行，请删除该子句后再运行。
-- 幂等：本脚本可重复执行。
-- ============================================================

USE `forum_db`;

-- ------------------------------------------------------------
-- 1. 添加"直接父回复"字段
--    NULL 表示一级回复（话题根）
-- ------------------------------------------------------------
ALTER TABLE `replies`
    ADD COLUMN IF NOT EXISTS `parent_id` INT DEFAULT NULL
    COMMENT '直接父回复 id（V3），NULL 表示一级回复' AFTER `content`;

-- ------------------------------------------------------------
-- 2. 添加"话题根回复"字段
--    一级回复在库中存 NULL，读取时由后端解析为其自身 id，便于直接按 root_id 聚合话题
-- ------------------------------------------------------------
ALTER TABLE `replies`
    ADD COLUMN IF NOT EXISTS `root_id` INT DEFAULT NULL
    COMMENT '所属话题根回复 id（V3），一级回复在库中存 NULL，读取时解析为其自身 id' AFTER `parent_id`;

-- ------------------------------------------------------------
-- 3. 建立索引（层级回溯 / 话题聚合查询）
-- ------------------------------------------------------------
CREATE INDEX IF NOT EXISTS `idx_reply_parent` ON `replies` (`parent_id`);
CREATE INDEX IF NOT EXISTS `idx_reply_root`   ON `replies` (`root_id`);

-- ------------------------------------------------------------
-- 4. 建立外键约束（防御性冗余，应用层已做级联删除）
--    新列默认全为 NULL，加约束不会影响存量数据
-- ------------------------------------------------------------
-- 直接父回复：删除父回复时级联删除子回复
SET @fk_parent_exists := (
    SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS
    WHERE CONSTRAINT_SCHEMA = 'forum_db'
      AND TABLE_NAME = 'replies'
      AND CONSTRAINT_NAME = 'fk_replies_parent'
);
SET @sql_parent := IF(
    @fk_parent_exists = 0,
    'ALTER TABLE `replies` ADD CONSTRAINT `fk_replies_parent` FOREIGN KEY (`parent_id`) REFERENCES `replies` (`id`) ON DELETE CASCADE ON UPDATE CASCADE',
    'SELECT 1'
);
PREPARE stmt FROM @sql_parent;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 话题根回复：根回复被删除时置空，避免误删整棵树
SET @fk_root_exists := (
    SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS
    WHERE CONSTRAINT_SCHEMA = 'forum_db'
      AND TABLE_NAME = 'replies'
      AND CONSTRAINT_NAME = 'fk_replies_root'
);
SET @sql_root := IF(
    @fk_root_exists = 0,
    'ALTER TABLE `replies` ADD CONSTRAINT `fk_replies_root` FOREIGN KEY (`root_id`) REFERENCES `replies` (`id`) ON DELETE SET NULL ON UPDATE CASCADE',
    'SELECT 1'
);
PREPARE stmt FROM @sql_root;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- ------------------------------------------------------------
-- 5. 历史数据回填（存量一级回复 root_id 保持 NULL，无需处理）
-- ------------------------------------------------------------
-- UPDATE `replies` SET `root_id` = `id` WHERE `parent_id` IS NOT NULL AND `root_id` IS NULL;
-- （上面这条仅对"直接子回复"成立；更深的层级请用应用层脚本回溯填充）

-- ------------------------------------------------------------
-- 验证新字段
-- ------------------------------------------------------------
-- DESCRIBE replies;
-- SHOW INDEX FROM replies;
