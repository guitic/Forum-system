-- ============================================================
-- 论坛系统 数据库初始化脚本 (MariaDB / MySQL)
-- 适配 Rocky Linux 生产环境
-- ============================================================

-- 创建数据库（如不存在）
CREATE DATABASE IF NOT EXISTS `forum_db`
    DEFAULT CHARACTER SET utf8mb4
    DEFAULT COLLATE utf8mb4_unicode_ci;

USE `forum_db`;

-- ============================================================
-- 1. 用户表
-- ============================================================
CREATE TABLE IF NOT EXISTS `users` (
    `id`            INT         NOT NULL AUTO_INCREMENT  COMMENT '主键，自增',
    `username`      VARCHAR(64) NOT NULL                  COMMENT '用户名',
    `password_hash` VARCHAR(255) NOT NULL                 COMMENT 'Bcrypt 加密密码密文',
    `role`          VARCHAR(20) NOT NULL DEFAULT 'user'    COMMENT '角色：user / admin',
    `created_at`    DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '注册时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_username` (`username`),
    INDEX `idx_username` (`username`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户表';

-- ============================================================
-- 2. 主贴表
-- ============================================================
CREATE TABLE IF NOT EXISTS `posts` (
    `id`         INT          NOT NULL AUTO_INCREMENT    COMMENT '主键，自增',
    `user_id`    INT          NOT NULL                   COMMENT '外键，关联 users.id',
    `title`      VARCHAR(200) NOT NULL                   COMMENT '帖子标题',
    `content`    TEXT         NOT NULL                   COMMENT 'Markdown 纯文本源码',
    `created_at` DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '发布时间',
    PRIMARY KEY (`id`),
    KEY `fk_posts_user` (`user_id`),
    CONSTRAINT `fk_posts_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`)
        ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='主贴表';

-- ============================================================
-- 3. 回帖表
-- ============================================================
CREATE TABLE IF NOT EXISTS `replies` (
    `id`         INT      NOT NULL AUTO_INCREMENT        COMMENT '主键，自增',
    `post_id`    INT      NOT NULL                       COMMENT '外键，关联 posts.id',
    `user_id`    INT      NOT NULL                       COMMENT '外键，关联 users.id',
    `content`    TEXT     NOT NULL                       COMMENT '回复内容（支持纯文本/Markdown）',
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '回复时间',
    PRIMARY KEY (`id`),
    KEY `fk_replies_post` (`post_id`),
    KEY `fk_replies_user` (`user_id`),
    KEY `idx_post_id` (`post_id`),
    CONSTRAINT `fk_replies_post` FOREIGN KEY (`post_id`) REFERENCES `posts` (`id`)
        ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT `fk_replies_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`)
        ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='回帖表';

-- ============================================================
-- 4. 创建应用专用数据库用户（如不存在）
--    密码请在生产环境中替换为强密码
-- ============================================================
-- CREATE USER IF NOT EXISTS 'forum_user'@'localhost' IDENTIFIED BY 'forum_pass';
-- GRANT ALL PRIVILEGES ON `forum_db`.* TO 'forum_user'@'localhost';
-- FLUSH PRIVILEGES;

-- ============================================================
-- 5. 插入默认管理员账号（可选）
--    默认密码为 admin123，请登录后立即修改
--    password_hash 为 bcrypt 哈希值（已预计算）
-- ============================================================
-- INSERT IGNORE INTO `users` (`username`, `password_hash`, `role`)
-- VALUES (
--     'admin',
--     '$2b$12$LJ3m4ys3LqVJXbY0nLXhVuG6r8x9Y0iH9sVbVhZ0nLXhVuG6r8x9',  -- 占位符，请使用 init_db.py 创建
--     'admin'
-- );

-- ============================================================
-- 验证：查询表结构
-- ============================================================
-- SHOW TABLES;
-- DESCRIBE users;
-- DESCRIBE posts;
-- DESCRIBE replies;
