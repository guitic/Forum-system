#!/usr/bin/env bash
# ============================================================================
# 论坛系统 Git 一键更新脚本
# 部署位置: /opt/forum/scripts/git_pull.sh
# 说明:
#   - 拉取 Gitee 最新代码
#   - 安装/更新 Python 依赖
#   - 执行数据库迁移 (Alembic / SQLAlchemy)
#   - 重启 systemd 后端服务
#   - 错误自动回滚 + 日志记录
#   - 可通过 crontab 定时执行，也可手动执行
# 用法:
#   ./git_pull.sh            # 完整更新
#   ./git_pull.sh --dry-run  # 仅拉取，不重启
#   ./git_pull.sh --rollback # 回滚到上一个版本
#   ./git_pull.sh --clean    # 拉取后执行清理
# ============================================================================

set -euo pipefail
IFS=$'\n\t'

# ---------------------------- 路径与配置 ----------------------------
APP_ROOT="${FORUM_APP_ROOT:-/opt/forum}"
REPO_DIR="${FORUM_REPO_DIR:-$APP_ROOT/repo}"
BACKEND_DIR="${FORUM_BACKEND_DIR:-$REPO_DIR/backend}"
FRONTEND_DIR="${FORUM_FRONTEND_DIR:-$REPO_DIR/frontend}"
VENV_DIR="${FORUM_VENV_DIR:-$APP_ROOT/venv}"
LOG_DIR="${FORUM_LOG_DIR:-/var/log/forum}"
LOG_FILE="$LOG_DIR/git_pull.log"
LOCK_FILE="/var/run/forum-update.lock"
SERVICE_NAME="${FORUM_SERVICE_NAME:-forum-api}"
GIT_BRANCH="${FORUM_GIT_BRANCH:-main}"

# 颜色输出 (非 TTY 自动禁用)
if [[ -t 1 ]]; then
    RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[0;33m'
    BLUE=$'\033[0;34m'; CYAN=$'\033[0;36m'; NC=$'\033[0m'
else
    RED=''; GREEN=''; YELLOW=''; BLUE=''; CYAN=''; NC=''
fi

DRY_RUN=false
ROLLBACK=false
CLEAN=false

# ---------------------------- 解析参数 ----------------------------
for arg in "$@"; do
    case "$arg" in
        --dry-run)   DRY_RUN=true ;;
        --rollback)  ROLLBACK=true ;;
        --clean)     CLEAN=true ;;
        -h|--help)
            grep '^#' "$0" | sed 's/^# \?//'
            exit 0
            ;;
        *)
            echo "${RED}未知参数: $arg${NC}" >&2
            exit 2
            ;;
    esac
done

# ---------------------------- 日志函数 ----------------------------
mkdir -p "$LOG_DIR"
touch "$LOG_FILE"

log() {
    local level="$1"; shift
    local ts
    ts="$(date '+%Y-%m-%d %H:%M:%S %z')"
    local msg="[$ts] [$level] $*"
    echo "$msg" | tee -a "$LOG_FILE"
}
info()    { log "INFO"    "$@"; }
warn()    { log "WARN"    "$YELLOW$*${NC}"; }
error()   { log "ERROR"   "$RED$*${NC}"; }
success() { log "OK"      "$GREEN$*${NC}"; }
section() { log "INFO"    "${CYAN}==== $* ====${NC}"; }

# ---------------------------- 错误处理 ----------------------------
cleanup() {
    local exit_code=$?
    if [[ -f "$LOCK_FILE" ]]; then
        rm -f "$LOCK_FILE"
    fi
    if [[ $exit_code -ne 0 ]]; then
        error "脚本以非零退出码 $exit_code 终止"
        # 失败时尝试回滚服务
        if [[ "$DRY_RUN" == false && "$ROLLBACK" == false ]]; then
            warn "尝试恢复服务到上一稳定版本..."
            if [[ -d "$REPO_DIR/.git" ]]; then
                cd "$REPO_DIR"
                if git log --oneline -2 | head -n 1 | grep -q 'HEAD'; then
                    # 回滚一次提交
                    git revert --no-edit HEAD 2>/dev/null || true
                fi
            fi
            systemctl restart "$SERVICE_NAME" 2>/dev/null || true
        fi
    fi
    exit $exit_code
}
trap cleanup EXIT

# ---------------------------- 前置检查 ----------------------------
preflight() {
    section "前置检查"

    # 仅 root 可执行
    if [[ $EUID -ne 0 ]]; then
        error "请以 root 或 sudo 执行此脚本"
        exit 3
    fi

    # 检查必要命令
    local missing=()
    for cmd in git python3 pip systemctl journalctl; do
        if ! command -v "$cmd" >/dev/null 2>&1; then
            missing+=("$cmd")
        fi
    done
    if [[ ${#missing[@]} -gt 0 ]]; then
        error "缺少命令: ${missing[*]}"
        exit 4
    fi

    # 检查代码目录
    if [[ ! -d "$REPO_DIR/.git" ]]; then
        error "仓库目录不存在或无 .git: $REPO_DIR"
        exit 5
    fi

    # 检查虚拟环境
    if [[ ! -x "$VENV_DIR/bin/python" ]]; then
        error "虚拟环境不存在: $VENV_DIR"
        exit 6
    fi

    # 检查服务状态
    if ! systemctl list-unit-files | grep -q "^$SERVICE_NAME\." ; then
        error "systemd 服务未安装: $SERVICE_NAME"
        exit 7
    fi

    success "前置检查通过"
}

# ---------------------------- 锁文件 ----------------------------
acquire_lock() {
    if [[ -f "$LOCK_FILE" ]]; then
        local pid
        pid=$(cat "$LOCK_FILE" 2>/dev/null || echo "")
        if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
            error "已有更新进程运行 (PID: $pid)"
            exit 8
        else
            warn "清理过期锁文件"
            rm -f "$LOCK_FILE"
        fi
    fi
    echo $$ > "$LOCK_FILE"
    info "已获取锁文件: $LOCK_FILE (PID: $$)"
}

# ---------------------------- Git 拉取 ----------------------------
git_pull() {
    section "Git 拉取最新代码"
    cd "$REPO_DIR"

    # 记录当前 commit 便于回滚
    local old_commit
    old_commit="$(git rev-parse HEAD 2>/dev/null || echo 'unknown')"
    export OLD_COMMIT="$old_commit"
    info "当前 HEAD: $old_commit"

    # 清理工作区
    git reset --hard 2>/dev/null || true
    git clean -fd 2>/dev/null || true

    # 获取远端
    info "同步远端分支: $GIT_BRANCH"
    if ! git fetch --all --prune --tags; then
        error "git fetch 失败"
        exit 10
    fi

    # 切到目标分支
    if ! git checkout "$GIT_BRANCH" 2>/dev/null; then
        error "切换到分支 $GIT_BRANCH 失败"
        exit 11
    fi

    # 拉取合并
    local before_count after_count
    before_count="$(git rev-list --count HEAD)"
    if ! git pull --ff-only origin "$GIT_BRANCH"; then
        error "git pull 失败 (可能存在冲突或非快进提交)"
        exit 12
    fi
    after_count="$(git rev-list --count HEAD)"
    local new_commits=$((after_count - before_count))
    local new_commit
    new_commit="$(git rev-parse --short HEAD)"
    info "拉取完成，新增 $new_commits 个提交，新 HEAD: $new_commit"

    if [[ $new_commits -eq 0 ]]; then
        warn "无新提交，跳过后续步骤"
        return 0
    fi
    return 0
}

# ---------------------------- 依赖安装 ----------------------------
install_deps() {
    section "安装/更新 Python 依赖"
    cd "$BACKEND_DIR"

    # 升级 pip / setuptools / wheel
    "$VENV_DIR/bin/pip" install --upgrade pip setuptools wheel 2>&1 | tee -a "$LOG_FILE"

    # 安装 requirements.txt (若存在)
    if [[ -f "requirements.txt" ]]; then
        info "安装 requirements.txt"
        "$VENV_DIR/bin/pip" install --requirement requirements.txt \
            --no-cache-dir \
            --disable-pip-version-check 2>&1 | tee -a "$LOG_FILE"
    else
        warn "未找到 requirements.txt，跳过依赖安装"
    fi

    # 可选: pyproject.toml (PEP 517)
    if [[ -f "pyproject.toml" ]]; then
        info "通过 pyproject.toml 安装"
        "$VENV_DIR/bin/pip" install --editable . \
            --no-cache-dir \
            --disable-pip-version-check 2>&1 | tee -a "$LOG_FILE"
    fi

    success "依赖安装完成"
}

# ---------------------------- 数据库迁移 ----------------------------
run_migrations() {
    section "执行数据库迁移"
    cd "$BACKEND_DIR"

    local migration_ok=true

    # 方案一: Alembic (Flask 常用)
    if [[ -f "alembic.ini" ]]; then
        info "执行 Alembic 迁移 (upgrade head)"
        if ! "$VENV_DIR/bin/python" -m alembic upgrade head 2>&1 | tee -a "$LOG_FILE"; then
            error "Alembic 迁移失败"
            migration_ok=false
        fi
    # 方案二: Flask-Migrate (CLI 子命令)
    elif [[ -f "migrations" ]] && [[ -d "migrations" ]]; then
        info "执行 Flask-Migrate 升级"
        if ! "$VENV_DIR/bin/FLASK_APP=app.py flask db upgrade" 2>&1 | tee -a "$LOG_FILE"; then
            error "Flask-Migrate 升级失败"
            migration_ok=false
        fi
    # 方案三: 自定义脚本 (scripts/migrate.py)
    elif [[ -f "scripts/migrate.py" ]]; then
        info "执行自定义迁移脚本"
        if ! "$VENV_DIR/bin/python" scripts/migrate.py 2>&1 | tee -a "$LOG_FILE"; then
            error "自定义迁移失败"
            migration_ok=false
        fi
    else
        warn "未发现迁移配置 (alembic.ini / migrations / scripts/migrate.py)，跳过"
    fi

    if [[ "$migration_ok" == true ]]; then
        success "数据库迁移完成"
    else
        error "数据库迁移失败，请人工介入"
        exit 20
    fi
}

# ---------------------------- 前端构建 (可选) ----------------------------
build_frontend() {
    section "构建前端资源"
    cd "$FRONTEND_DIR"

    if [[ -f "package.json" ]]; then
        info "安装前端依赖 (npm ci)"
        npm ci --silent 2>&1 | tee -a "$LOG_FILE"
        info "构建前端 (npm run build)"
        npm run build --silent 2>&1 | tee -a "$LOG_FILE"
        # 拷贝到 Nginx 静态目录
        local target="/opt/forum/frontend"
        if [[ -d "dist" ]]; then
            rsync -a --delete dist/ "$target/" 2>/dev/null || cp -rf dist/* "$target/"
        fi
        success "前端构建完成"
    else
        info "前端为纯静态文件，无需构建"
    fi
}

# ---------------------------- 权限修正 ----------------------------
fix_permissions() {
    section "修正文件权限"
    if id forum >/dev/null 2>&1; then
        chown -R forum:forum "$REPO_DIR" "$APP_ROOT/logs" 2>/dev/null || true
    fi
    chmod 750 "$BACKEND_DIR" "$FRONTEND_DIR" 2>/dev/null || true
    chmod 700 "$VENV_DIR" 2>/dev/null || true
    success "权限修正完成"
}

# ---------------------------- 清理 ----------------------------
do_clean() {
    section "清理临时文件"
    find "$REPO_DIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    find "$REPO_DIR" -type f -name "*.pyc" -delete 2>/dev/null || true
    find "$LOG_DIR" -type f -name "*.log" -mtime +30 -delete 2>/dev/null || true
    rm -rf "$REPO_DIR"/.pytest_cache 2>/dev/null || true
    success "清理完成"
}

# ---------------------------- 重启服务 ----------------------------
restart_service() {
    section "重启 $SERVICE_NAME 服务"

    # 重载 systemd 配置 (unit 文件可能更新)
    systemctl daemon-reload

    # 重载 Nginx (配置可能更新)
    if command -v nginx >/dev/null 2>&1; then
        if nginx -t 2>&1 | tee -a "$LOG_FILE" | grep -q "syntax is ok"; then
            systemctl reload nginx 2>&1 | tee -a "$LOG_FILE" || true
            info "Nginx 配置已重载"
        else
            error "Nginx 配置语法错误，跳过重载"
        fi
    fi

    # 重启后端
    systemctl restart "$SERVICE_NAME" 2>&1 | tee -a "$LOG_FILE"

    # 等待服务就绪
    local retries=0
    while [[ $retries -lt 10 ]]; do
        if systemctl is-active --quiet "$SERVICE_NAME"; then
            success "$SERVICE_NAME 已激活"
            break
        fi
        retries=$((retries + 1))
        info "等待服务就绪... ($retries/10)"
        sleep 2
    done

    if ! systemctl is-active --quiet "$SERVICE_NAME"; then
        error "$SERVICE_NAME 启动失败"
        journalctl -u "$SERVICE_NAME" -n 30 --no-pager 2>&1 | tee -a "$LOG_FILE"
        exit 30
    fi

    # 健康检查 (HTTP 探活)
    if command -v curl >/dev/null 2>&1; then
        for i in {1..5}; do
            if curl -sf http://127.0.0.1:5000/healthz >/dev/null 2>&1; then
                success "后端健康检查通过"
                break
            fi
            info "健康检查失败，重试 ($i/5)"
            sleep 2
        done
    fi

    # 打印最近日志
    info "最近 20 条服务日志:"
    journalctl -u "$SERVICE_NAME" -n 20 --no-pager 2>&1 | tee -a "$LOG_FILE" || true
}

# ---------------------------- 回滚 ----------------------------
do_rollback() {
    section "执行回滚"
    cd "$REPO_DIR"

    if ! git rev-parse HEAD~1 >/dev/null 2>&1; then
        error "无上一版本可回滚"
        exit 40
    fi

    local target
    target="$(git rev-parse HEAD~1)"
    info "回滚到: $target"
    git checkout "$target"
    install_deps
    run_migrations
    restart_service
    success "回滚完成"
}

# ---------------------------- 主流程 ----------------------------
main() {
    section "论坛系统更新脚本启动"
    info "参数: DRY_RUN=$DRY_RUN ROLLBACK=$ROLLBACK CLEAN=$CLEAN"
    info "时间: $(date '+%F %T %z')"

    acquire_lock
    preflight

    if [[ "$ROLLBACK" == true ]]; then
        do_rollback
        exit 0
    fi

    git_pull

    # dry-run 仅到拉取即止
    if [[ "$DRY_RUN" == true ]]; then
        warn "dry-run 模式，跳过后续步骤"
        exit 0
    fi

    install_deps
    run_migrations
    build_frontend
    fix_permissions

    if [[ "$CLEAN" == true ]]; then
        do_clean
    fi

    restart_service

    section "更新完成"
    success "论坛系统已成功更新至 $(cd "$REPO_DIR" && git rev-parse --short HEAD)"
}

main "$@"
