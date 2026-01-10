#!/bin/bash
set -e

echo "=========================================="
echo "  🚀 Uptime Kuma 启动中 (Lunes.host)"
echo "=========================================="

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_DIR="${APP_DIR}/data"
KUMA_DIR="${APP_DIR}/uptime-kuma"
AGENT_NAME="nezha-agent"
AGENT_PID_FILE="${APP_DIR}/.agent.pid"
CONFIG_FILE="${APP_DIR}/.agent.yaml"
WATCHDOG_PID_FILE="${APP_DIR}/.watchdog.pid"

[ -f "${APP_DIR}/config.sh" ] && source "${APP_DIR}/config.sh" && echo "✓ 配置已加载"

mkdir -p "$DATA_DIR"
export DATA_DIR

# =========================
# 进程管理函数（不依赖 ps 命令）
# =========================
find_process_by_name() {
    local name="$1"
    for pid in /proc/[0-9]*; do
        pid=${pid##*/}
        if [ -f "/proc/$pid/cmdline" ] 2>/dev/null; then
            if grep -q "$name" "/proc/$pid/cmdline" 2>/dev/null; then
                echo "$pid"
                return 0
            fi
        fi
    done
    return 1
}

kill_process_by_name() {
    local name="$1"
    for pid in /proc/[0-9]*; do
        pid=${pid##*/}
        if [ -f "/proc/$pid/cmdline" ] 2>/dev/null; then
            if grep -q "$name" "/proc/$pid/cmdline" 2>/dev/null; then
                kill $pid 2>/dev/null || true
            fi
        fi
    done
}

# =========================
# 清理函数
# =========================
cleanup() {
    echo ""
    echo "[INFO] 正在清理..."
    
    if [ -f "$WATCHDOG_PID_FILE" ]; then
        WATCHDOG_PID=$(cat "$WATCHDOG_PID_FILE")
        kill $WATCHDOG_PID 2>/dev/null && echo "✓ 守护进程已停止"
        rm -f "$WATCHDOG_PID_FILE"
    fi
    
    if [ -f "$AGENT_PID_FILE" ]; then
        AGENT_PID=$(cat "$AGENT_PID_FILE")
        kill $AGENT_PID 2>/dev/null && echo "✓ 哪吒 Agent 已停止"
        rm -f "$AGENT_PID_FILE"
    fi
    
    rm -f "$CONFIG_FILE"
    exit 0
}
trap cleanup SIGTERM SIGINT EXIT

# =========================
# 下载预构建版本
# =========================
if [ ! -f "$KUMA_DIR/server/server.js" ]; then
    
    if [ -z "${KUMA_DOWNLOAD_URL:-}" ]; then
        echo "[ERROR] 请设置 KUMA_DOWNLOAD_URL"
        exit 1
    fi
    
    echo "[INFO] 下载 Uptime Kuma..."
    rm -rf "$KUMA_DIR"
    mkdir -p "$KUMA_DIR"
    curl -sL "$KUMA_DOWNLOAD_URL" | tar -xz --strip-components=1 -C "$KUMA_DIR"
    
    if [ ! -f "$KUMA_DIR/server/server.js" ]; then
        echo "[ERROR] 下载失败"
        exit 1
    fi
    echo "✓ 下载完成"
fi

if [ ! -d "$KUMA_DIR/node_modules" ]; then
    echo "[ERROR] node_modules 不存在"
    exit 1
fi

# =========================
# 哪吒 Agent 配置检查
# =========================
NEZHA_ENABLED=false

if [ -n "${NZ_SERVER:-}" ] && [ -n "${NZ_UUID:-}" ] && [ -n "${NZ_CLIENT_SECRET:-}" ]; then
    NEZHA_ENABLED=true
    echo "✓ 哪吒监控配置完整"
elif [ -n "${NZ_SERVER:-}" ] || [ -n "${NZ_UUID:-}" ] || [ -n "${NZ_CLIENT_SECRET:-}" ]; then
    echo "[WARN] 哪吒配置不完整"
    [ -z "${NZ_SERVER:-}" ] && echo "       - 缺少 NZ_SERVER"
    [ -z "${NZ_UUID:-}" ] && echo "       - 缺少 NZ_UUID"
    [ -z "${NZ_CLIENT_SECRET:-}" ] && echo "       - 缺少 NZ_CLIENT_SECRET"
fi

# =========================
# 下载哪吒 Agent
# =========================
if [ "$NEZHA_ENABLED" = true ] && [ ! -x "${APP_DIR}/${AGENT_NAME}" ]; then
    echo "[INFO] 下载哪吒监控 Agent..."
    
    cd "${APP_DIR}"
    arch=$(uname -m)
    case $arch in
        x86_64)  fileagent="nezha-agent_linux_amd64.zip" ;;
        aarch64) fileagent="nezha-agent_linux_arm64.zip" ;;
        s390x)   fileagent="nezha-agent_linux_s390x.zip" ;;
        *) 
            echo "[WARN] 不支持的架构: $arch，哪吒 Agent 将不会启动"
            NEZHA_ENABLED=false
            fileagent="" 
            ;;
    esac
    
    if [ -n "$fileagent" ]; then
        # 获取版本号（多种方法）
        if [ -z "${NZ_AGENT_VERSION:-}" ] || [ "${NZ_AGENT_VERSION}" = "latest" ]; then
            echo "[INFO] 获取最新版本..."
            
            # 方法1: GitHub API
            NZ_AGENT_VERSION=$(curl -sL --connect-timeout 10 "https://api.github.com/repos/nezhahq/agent/releases/latest" 2>/dev/null | grep '"tag_name"' | head -1 | awk -F'"' '{print $4}')
            
            # 方法2: 通过 GitHub 重定向
            if [ -z "$NZ_AGENT_VERSION" ]; then
                NZ_AGENT_VERSION=$(curl -sIL --connect-timeout 10 "https://github.com/nezhahq/agent/releases/latest" 2>/dev/null | grep -i "^location:" | tail -1 | sed 's/.*\/tag\///' | tr -d '\r\n ')
            fi
            
            # 方法3: 默认版本
            if [ -z "$NZ_AGENT_VERSION" ]; then
                NZ_AGENT_VERSION="v1.12.1"
                echo "[INFO] 使用默认版本: $NZ_AGENT_VERSION"
            fi
        fi
        
        echo "[INFO] Agent 版本: $NZ_AGENT_VERSION"
        URL="https://github.com/nezhahq/agent/releases/download/${NZ_AGENT_VERSION}/${fileagent}"
        
        if curl -sL --connect-timeout 30 --retry 2 "$URL" -o "$fileagent" && [ -s "$fileagent" ]; then
            unzip -qo "$fileagent" -d . && rm -f "$fileagent"
            mv ./nezha-agent "./${AGENT_NAME}" 2>/dev/null || true
            chmod +x "./${AGENT_NAME}"
            echo "✓ 哪吒 Agent 下载完成 (${NZ_AGENT_VERSION})"
        else
            echo "[WARN] 哪吒 Agent 下载失败，Agent 将不会启动"
            rm -f "$fileagent" 2>/dev/null
            NEZHA_ENABLED=false
        fi
    fi
fi

# =========================
# 哪吒 Agent 启动函数
# =========================
start_nezha_agent() {
    if [ ! -x "${APP_DIR}/${AGENT_NAME}" ] || [ ! -f "${CONFIG_FILE}" ]; then
        return 1
    fi
    
    # 杀掉可能存在的旧进程（通过 PID 文件）
    if [ -f "$AGENT_PID_FILE" ]; then
        OLD_PID=$(cat "$AGENT_PID_FILE")
        kill $OLD_PID 2>/dev/null || true
        rm -f "$AGENT_PID_FILE"
    fi
    
    # 确保清理干净（使用 /proc 文件系统）
    kill_process_by_name "${AGENT_NAME}"
    sleep 1
    
    # 启动新进程
    nohup "${APP_DIR}/${AGENT_NAME}" -c "${CONFIG_FILE}" >/dev/null 2>&1 &
    local NEW_PID=$!
    echo $NEW_PID > "$AGENT_PID_FILE"
    
    sleep 3
    
    if kill -0 $NEW_PID 2>/dev/null; then
        return 0
    else
        rm -f "$AGENT_PID_FILE"
        return 1
    fi
}

# =========================
# 检查 Agent 是否运行
# =========================
is_agent_running() {
    # 优先检查 PID 文件
    if [ -f "$AGENT_PID_FILE" ]; then
        local PID=$(cat "$AGENT_PID_FILE")
        if kill -0 $PID 2>/dev/null; then
            return 0
        fi
    fi
    
    # 使用 /proc 文件系统查找进程
    local FOUND_PID=$(find_process_by_name "${AGENT_NAME}")
    if [ -n "$FOUND_PID" ]; then
        echo "$FOUND_PID" > "$AGENT_PID_FILE"
        return 0
    fi
    
    return 1
}

# =========================
# 生成配置并启动哪吒 Agent
# =========================
if [ "$NEZHA_ENABLED" = true ] && [ -x "${APP_DIR}/${AGENT_NAME}" ]; then
    echo "[INFO] 启动哪吒监控 Agent..."
    
    cat > "${CONFIG_FILE}" <<EOF
client_secret: ${NZ_CLIENT_SECRET}
debug: false
disable_auto_update: true
disable_command_execute: false
disable_force_update: true
disable_nat: false
disable_send_query: false
gpu: false
insecure_tls: false
ip_report_period: 1800
report_delay: 4
server: ${NZ_SERVER}
skip_connection_count: false
skip_procs_count: false
temperature: false
tls: ${NZ_TLS:-false}
use_gitee_to_upgrade: false
use_ipv6_country_code: false
uuid: ${NZ_UUID}
EOF
    
    if start_nezha_agent; then
        echo "✓ 哪吒 Agent 已启动 (PID: $(cat $AGENT_PID_FILE))"
    else
        echo "[WARN] 哪吒 Agent 启动失败"
    fi
fi

# =========================
# 哪吒 Agent 守护进程
# =========================
if [ "$NEZHA_ENABLED" = true ] && [ -f "$CONFIG_FILE" ]; then
    (
        CHECK_INTERVAL=${NZ_CHECK_INTERVAL:-30}
        MAX_RESTARTS=30
        RESTART_COUNT=0
        HOUR_START=$(date +%s)
        
        sleep 10
        
        while true; do
            sleep $CHECK_INTERVAL
            
            if ! is_agent_running; then
                NOW=$(date +%s)
                if [ $((NOW - HOUR_START)) -gt 3600 ]; then
                    RESTART_COUNT=0
                    HOUR_START=$NOW
                fi
                
                if [ $RESTART_COUNT -ge $MAX_RESTARTS ]; then
                    echo "[$(date '+%H:%M:%S')] 重启过多，休眠 30 分钟"
                    sleep 1800
                    RESTART_COUNT=0
                    HOUR_START=$(date +%s)
                    continue
                fi
                
                start_nezha_agent && echo "[$(date '+%H:%M:%S')] ✓ Agent 已重启"
                RESTART_COUNT=$((RESTART_COUNT + 1))
            fi
        done
    ) &
    WATCHDOG_PID=$!
    echo $WATCHDOG_PID > "$WATCHDOG_PID_FILE"
    echo "✓ 哪吒 Agent 守护进程已启动 (每 ${NZ_CHECK_INTERVAL:-30}s 检查)"
fi

# =========================
# 首次启动恢复备份
# =========================
if [ -n "${WEBDAV_URL:-}" ] && [ ! -f "$DATA_DIR/kuma.db" ]; then
    echo "[INFO] 首次启动，检查 WebDAV 备份..."
    bash "${APP_DIR}/scripts/restore.sh" || echo "[WARN] 恢复失败或无备份"
fi

# =========================
# 备份守护进程
# =========================
if [ -n "${WEBDAV_URL:-}" ]; then
    (
        while true; do
            sleep 3600
            current_date=$(date +"%Y-%m-%d")
            current_hour=$(date +"%H")
            LAST_BACKUP_FILE="/tmp/last_backup_date"
            [ -f "$LAST_BACKUP_FILE" ] && last_backup_date=$(cat "$LAST_BACKUP_FILE") || last_backup_date=""
            
            if [ "$current_hour" -eq "${BACKUP_HOUR:-4}" ] && [ "$last_backup_date" != "$current_date" ]; then
                echo "[INFO] 执行每日备份..."
                bash "${APP_DIR}/scripts/backup.sh" && echo "$current_date" > "$LAST_BACKUP_FILE"
            fi
        done
    ) &
    echo "✓ 备份守护进程已启动 (每天 ${BACKUP_HOUR:-4}:00)"
fi

# =========================
# 启动 Uptime Kuma
# =========================
echo "[INFO] 启动 Uptime Kuma..."

export UPTIME_KUMA_PORT="${PORT:-3001}"
export NODE_OPTIONS="--max-old-space-size=256"

echo "=========================================="
echo "  端口: $UPTIME_KUMA_PORT"
echo "  数据: $DATA_DIR"
if [ "$NEZHA_ENABLED" = true ]; then
    echo "  哪吒: 已启用 + 守护 (每 ${NZ_CHECK_INTERVAL:-30}s)"
else
    echo "  哪吒: 未启用"
fi
echo "=========================================="

cd "$KUMA_DIR"
exec node server/server.js
