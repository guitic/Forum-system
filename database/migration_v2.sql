-- ============================================================
-- V1.1 → V2.0 数据库迁移脚本
-- 为已存在的数据库添加用户中心新字段
-- ============================================================

USE `forum_db`;

-- 添加昵称字段
ALTER TABLE `users`
    ADD COLUMN IF NOT EXISTS `nickname` VARCHAR(50) DEFAULT NULL
    COMMENT '显示昵称（V2），为空时回退 username' AFTER `role`;

-- 添加个人简介字段
ALTER TABLE `users`
    ADD COLUMN IF NOT EXISTS `bio` TEXT DEFAULT NULL
    COMMENT '个人简介（V2），最多 200 字符' AFTER `nickname`;

-- 添加头像路径字段
ALTER TABLE `users`
    ADD COLUMN IF NOT EXISTS `avatar_url` VARCHAR(255) DEFAULT NULL
    COMMENT '头像路径（V2），如 /uploads/avatars/xxx.png' AFTER `bio`;

-- 验证新字段
-- DESCRIBE users;
