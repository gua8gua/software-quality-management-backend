#!/usr/bin/env bash
#
# 软件质量管理后端一键冒烟测试
# -----------------------------------------------------------
# 覆盖全链路: 存活探测 -> 建知识库 -> 上传文档 -> 向量检索
#
# 用法:
#   bash test_api.sh                      # 默认 http://localhost:8000
#   API_BASE=http://1.2.3.4:8000 bash test_api.sh
#
# 依赖: curl (bash / Git Bash / WSL 自带)
# 说明: 每次运行都会新建一个带时间戳后缀的知识库, 不会互相冲突;
#       由于服务没有提供删除接口, 测试数据会累积在 postgres 中。
# -----------------------------------------------------------

set -u

API_BASE="${API_BASE:-http://localhost:8000}"
TENANT="tenant-smoke"
RUN_ID="$(date +%Y%m%d-%H%M%S)"
KB_NAME="smoke-kb-${RUN_ID}"

# ---------- 颜色 ----------
if [ -t 1 ]; then
  GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
else
  GREEN=''; RED=''; YELLOW=''; CYAN=''; NC=''
fi

PASS=0
FAIL=0

pass() { printf "${GREEN}[PASS]${NC} %s\n" "$1"; PASS=$((PASS + 1)); }
fail() { printf "${RED}[FAIL]${NC} %s\n" "$1"; FAIL=$((FAIL + 1)); }
info() { printf "${CYAN}[INFO]${NC} %s\n" "$1"; }
warn() { printf "${YELLOW}[WARN]${NC} %s\n" "$1"; }

# 从 JSON 中提取某个 key 的值(兼容字符串和数字, 纯文本无额外依赖)
json_str() {
  printf '%s' "$1" \
    | grep -oE "\"$2\":(\"[^\"]*\"|-?[0-9]+(\.[0-9]+)?)" \
    | head -n1 \
    | sed -E "s/\"$2\"://; s/\"//g"
}

printf "\n%s\n" "==================================================="
info "软件质量管理后端冒烟测试  @  ${API_BASE}   (run: ${RUN_ID})"
printf "%s\n\n" "==================================================="

# ---------- 前置检查 ----------
if ! command -v curl >/dev/null 2>&1; then
  fail "未找到 curl, 请先安装。"
  exit 1
fi

# ---------- 1. 存活探测 ----------
info "1/5  存活探测  GET /openapi.json"
HTTP_CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "${API_BASE}/openapi.json" 2>/dev/null)"
if [ "$HTTP_CODE" = "200" ]; then
  pass "API 可达 (HTTP 200)"
else
  fail "API 不可达 (HTTP ${HTTP_CODE:-无响应})"
  warn "请确认已运行: docker compose up  (且 Docker Desktop 引擎已启动)"
  printf "\n${RED}测试中止: 服务未启动, 无法继续。${NC}\n"
  exit 1
fi

# ---------- 2. 创建知识库 ----------
info "2/5  创建知识库  POST /api/v1/knowledge-bases/create"
CREATE_RESP="$(curl -s --max-time 10 -X POST "${API_BASE}/api/v1/knowledge-bases/create" \
  -H 'Content-Type: application/json' \
  -d "{\"name\":\"${KB_NAME}\",\"description\":\"created by test_api.sh\",\"tenant_id\":\"${TENANT}\"}")"
KB_ID="$(json_str "$CREATE_RESP" kb_id)"
if [ -n "$KB_ID" ]; then
  pass "知识库已创建  kb_id=${KB_ID}"
else
  fail "创建知识库失败"
  printf "       响应: %s\n" "$CREATE_RESP"
  exit 1
fi

# ---------- 3. 上传文档 ----------
info "3/5  上传文档  POST /api/v1/documents/upload"
UPLOAD_RESP="$(curl -s --max-time 15 -X POST "${API_BASE}/api/v1/documents/upload" \
  -H 'Content-Type: application/json' \
  -d "{\"tenant_id\":\"${TENANT}\",\"kb_id\":\"${KB_ID}\",\"title\":\"Docker Basics\",\"content\":\"Docker is a platform for building and running containers. Docker Compose lets you define multi-container applications in a YAML file. The docker compose up command starts all defined services.\"}")"
DOC_ID="$(json_str "$UPLOAD_RESP" document_id)"
CHUNK_COUNT="$(json_str "$UPLOAD_RESP" chunk_count)"
if [ -n "$DOC_ID" ]; then
  pass "文档已入库  document_id=${DOC_ID}  chunk_count=${CHUNK_COUNT}"
else
  fail "上传文档失败"
  printf "       响应: %s\n" "$UPLOAD_RESP"
  exit 1
fi

# ---------- 4. 向量检索 ----------
info "4/5  向量检索  POST /api/v1/rag/retrieve"
RETRIEVE_RESP="$(curl -s --max-time 30 -X POST "${API_BASE}/api/v1/rag/retrieve" \
  -H 'Content-Type: application/json' \
  -d "{\"tenant_id\":\"${TENANT}\",\"kb_id\":\"${KB_ID}\",\"user_id\":\"user-smoke\",\"query\":\"What is Docker Compose used for?\"}")"
RETRIEVE_CODE="$(json_str "$RETRIEVE_RESP" code)"
# 检索结果里包含我们刚上传的内容, 说明 embedding + pgvector 全链路可用
if printf '%s' "$RETRIEVE_RESP" | grep -q "Docker Compose"; then
  pass "检索命中目标文档 (embedding + pgvector 正常)"
elif [ "$RETRIEVE_CODE" = "0" ]; then
  warn "接口返回成功但未命中目标内容, 请检查 embedding 模型配置"
  printf "       响应: %s\n" "$RETRIEVE_RESP"
else
  fail "检索失败"
  printf "       响应: %s\n" "$RETRIEVE_RESP"
fi

# ---------- 5. API 文档 ----------
info "5/5  在线文档  GET /docs"
DOCS_CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "${API_BASE}/docs" 2>/dev/null)"
if [ "$DOCS_CODE" = "200" ]; then
  pass "Swagger 文档可访问  ${API_BASE}/docs"
else
  fail "Swagger 文档不可达 (HTTP ${DOCS_CODE:-无响应})"
fi

# ---------- 汇总 ----------
printf "\n%s\n" "==================================================="
printf "结果汇总:  ${GREEN}%d 通过${NC}  /  ${RED}%d 失败${NC}\n" "$PASS" "$FAIL"
printf "%s\n\n" "==================================================="

[ "$FAIL" -eq 0 ] && exit 0 || exit 1
