# Skill 市场客户端对接文档

## 1. 服务地址

客户端只访问 `lea-gateway` 的公网地址，不直接访问 `lea-config`、MySQL 或 MinIO。

```text
客户端 -> lea-gateway -> lea-config -> MySQL / MinIO
```

下面的接口路径都以网关地址为前缀，例如：

```text
https://skill.example.com/api/user/skills
```

## 2. 身份认证

所有 Skill 接口都需要外部登录系统签发的 JWT：

```http
Authorization: Bearer <access_token>
X-Selected-Tenant-ID: <用户当前选择的 tenantId>
```

用户和租户是多对多关系，因此客户端必须先让用户选择租户，并在每次请求带上
`X-Selected-Tenant-ID`。网关会用同一个 Bearer JWT 调用租户关系接口确认用户属于该租户，
再根据 `tenantType` 和 `userTenantRoleName` 生成可信角色：`Super + 管理者` 为
`platform_admin`，`Regular + 管理者` 为 `admin`，编辑为 `user`。

JWT 由外部认证系统签发，客户端不生成、不修改，也不提交用户身份字段。网关通过 OIDC
JWKS 验证 RS256 签名、签发方和过期时间后读取：

```json
{
  "sub": "user-123",
  "TenantId": "tenant-001",
  "Role": ["user"],
  "exp": 1760000000
}
```

角色约定：

| 角色 | 权限 |
|---|---|
| `user` | 查看可见 Skill、上传和管理自己创建的 Skill、提交上架申请 |
| `admin` | 审核本租户的 `tenant` 上架申请 |
| `platform_admin` | 审核 `global` 上架申请 |

客户端不要传 `user_id`、`tenant_id`、`X-User-ID`、`X-Tenant-ID` 或 `X-User-Role`。

## 3. Skill 可见性

Skill 有三种可见范围：

| `visibility` | 含义 |
|---|---|
| `private` | 只有创建者可见 |
| `tenant` | 当前租户所有用户可见 |
| `global` | 所有租户用户可见 |

普通用户上传的 Skill 默认是 `private`。上传者不能在请求中自行指定可见范围，必须通过审核流程上架。

## 4. 查看 Skill

### 查看市场 Skill

```http
GET /api/user/skills?visibility=global&page=1&size=50
Authorization: Bearer <access_token>
```

### 查看我的 Skill

```http
GET /api/user/skills?visibility=private&page=1&size=50
Authorization: Bearer <access_token>
```

### 查看租户 Skill

```http
GET /api/user/skills?visibility=tenant&page=1&size=50
Authorization: Bearer <access_token>
```

也可以按分类过滤：

```http
GET /api/user/skills?visibility=global&category=productivity
```

响应：

```json
{
  "skills": [
    {
      "id": 12,
      "name": "pdf-helper",
      "display_name": "Pdf Helper",
      "description": "帮助用户处理 PDF 文件",
      "category": "productivity",
      "version": "1.0.0",
      "model_tier": "standard",
      "allowed_tools": ["read_file", "write_file"],
      "visibility": "global",
      "enabled": true,
      "is_builtin": false,
      "has_bundle": true,
      "is_own": false,
      "can_edit": false,
      "pending_publish": null,
      "system_prompt": "..."
    }
  ],
  "total": 1,
  "page": 1,
  "size": 50
}
```

`pending_publish` 为 `tenant` 或 `global` 时，表示该 Skill 有对应的待审核申请。

### 查看单个 Skill

```http
GET /api/user/skills/{skill_id}
Authorization: Bearer <access_token>
```

对用户不可见的 Skill 返回 `404`，不会返回 `403`，避免泄露资源是否存在。

## 5. 版本与发布

一个 Skill 是稳定的逻辑名称，上传一个新的 `version` 就创建一个不可变版本，旧版本不会被覆盖。
版本号由上传请求的 `version` 表单字段传入；同一个 Skill 不能重复上传同一版本。
每个版本还会记录发布说明、压缩包大小和 SHA-256：

```http
GET /api/user/skills/{skill_id}?version=1.0.0
GET /api/user/skills/{skill_id}/versions
GET /api/user/skills/{skill_id}/download?version=1.0.0&expires=600
```

`GET /api/user/skills/{skill_id}/versions` 返回版本列表，其中包含 `change_log`、`size_bytes`、`sha256`。
`GET /api/user/skills/{skill_id}?version=1.0.0` 返回指定版本的完整快照及这些发布信息。

不传 `version` 时，查询和下载都使用 Skill 当前版本。

## 6. 上传 Skill

```http
POST /api/user/skills/upload
Authorization: Bearer <access_token>
Content-Type: multipart/form-data
```

表单字段：

| 字段 | 类型 | 要求 |
|---|---|---|
| `file` | File | `.zip` 或 `.skill`，最大 500 MB |
| `version` | String | 必填，例如 `1.1.0` |
| `change_log` | String | 可选，本次版本修改说明 |

示例：

```bash
curl -X POST "https://skill.example.com/api/user/skills/upload" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -F "file=@pdf-helper.zip" \
  -F "version=1.1.0" \
  -F "change_log=增加 PDF 合并功能"
```

上传成功返回 HTTP `201` 和 Skill 对象。新 Skill 默认：

```json
{
  "visibility": "private",
  "enabled": true,
  "is_own": true,
  "can_edit": true,
  "pending_publish": null
}
```

## 7. 上传包格式

压缩包内必须包含 UTF-8 编码的 `SKILL.md`，可以放在根目录或一层子目录：

```text
pdf-helper.zip
├── SKILL.md
├── scripts/
│   └── extract.py
├── references/
│   └── guide.md
└── assets/
    └── template.docx
```

`SKILL.md` 使用 YAML frontmatter：

```markdown
---
name: pdf-helper
description: 帮助用户处理 PDF 文件
allowed-tools:
  - read_file
  - write_file
---

你是一个 PDF 处理专家。

处理任务时先确认用户目标，再使用合适的工具完成任务。
```

字段说明：

| 字段 | 必填 | 作用 |
|---|---|---|
| `name` | 是 | Skill 唯一名称，使用小写 kebab-case，例如 `pdf-helper` |
| `description` | 否 | 市场列表描述 |
| `allowed-tools` | 否 | Skill 需要的工具列表，可以是 YAML 数组或逗号分隔字符串 |
| frontmatter 后的 Markdown 正文 | 否 | Skill 的系统提示词 |

例如 `allowed-tools: read_file, write_file` 会被解析为：

```json
["read_file", "write_file"]
```

服务会解析 `SKILL.md` 的元数据并保存到数据库，同时把原始 ZIP 保存到对象存储。

## 8. 编辑和删除

只有创建者或拥有相应管理权限的用户可以编辑；Skill 名称不可修改。

```http
PUT /api/user/skills/{skill_id}
Authorization: Bearer <access_token>
Content-Type: application/json
```

可更新字段包括：

```json
{
  "display_name": "PDF 助手",
  "description": "新的描述",
  "category": "productivity",
  "system_prompt": "新的系统指令",
  "model_tier": "standard",
  "required_tools": ["read_file"],
  "enabled": true
}
```

删除：

```http
DELETE /api/user/skills/{skill_id}
Authorization: Bearer <access_token>
```

`enabled=false` 表示停用。停用不会改变可见性，但运行时不会继续下发该 Skill。

## 9. 申请上架

```http
POST /api/user/skills/{skill_id}/publish
Authorization: Bearer <access_token>
Content-Type: application/json
```

申请租户内可见：

```json
{
  "target_scope": "tenant",
  "note": "希望供本租户成员使用"
}
```

申请全平台可见：

```json
{
  "target_scope": "global",
  "note": "希望加入公共 Skill 市场"
}
```

同一个 Skill 同时只能有一份待审核申请。成功返回 HTTP `201`，并返回申请记录。

## 10. 查看审核申请

```http
GET /api/user/skills/publish-requests?status=pending
Authorization: Bearer <access_token>
```

返回内容取决于角色：

- 普通用户：只能看到自己提交的申请；
- `admin`：看到本租户待审核的 `tenant` 申请；
- `platform_admin`：看到待审核的 `global` 申请。

响应：

```json
{
  "requests": [
    {
      "id": 7,
      "skill_id": 12,
      "skill_name": "pdf-helper",
      "tenant_id": "tenant-001",
      "requested_by": "user-123",
      "target_scope": "tenant",
      "status": "pending",
      "review_note": "希望供本租户成员使用",
      "reviewed_by": null,
      "reviewed_at": null,
      "created_at": "2026-08-24T10:00:00"
    }
  ]
}
```

## 11. 审核申请

```http
POST /api/user/skills/publish-requests/{request_id}/review
Authorization: Bearer <admin_or_platform_admin_jwt>
Content-Type: application/json
```

通过：

```json
{
  "approve": true,
  "note": "审核通过"
}
```

驳回：

```json
{
  "approve": false,
  "note": "请补充使用说明"
}
```

审核规则：

| 申请类型 | 审核角色 | 通过后 |
|---|---|---|
| `tenant` | 租户管理员 | 当前租户所有用户可见 |
| `global` | 平台管理员 | 所有租户用户可见 |

已处理的申请不能重复审核。目标空间已有同名 Skill 时，审核会返回 `409`。

## 12. 下载 Skill

```http
GET /api/user/skills/{skill_id}/download?expires=600
Authorization: Bearer <access_token>
```

`expires` 可选，范围为 60 到 3600 秒，默认 600 秒。

响应：

```json
{
  "url": "https://objects.example.com/lea-uploads/...?X-Amz-Signature=...",
  "expires_in": 600,
  "filename": "pdf-helper.zip",
  "sha256": "..."
}
```

客户端拿到 `url` 后直接下载：

```ts
const response = await request.get(`/api/user/skills/${skillId}/download`)
window.location.assign(response.url)
```

下载 URL 是对象存储的预签名地址，不需要再次携带 JWT。客户端必须能够访问该对象存储公网地址。

## 13. 常见错误

| HTTP 状态码 | 含义 |
|---|---|
| `401` | 缺少、无效或过期的 JWT |
| `403` | 身份有效，但没有该操作权限 |
| `404` | Skill、申请不存在，或当前用户不可见 |
| `409` | 名称冲突、已有待审申请、申请已处理 |
| `413` | ZIP 超过 500 MB |
| `422` | `SKILL.md` 格式或请求字段不正确 |
| `502` / `503` | 网关、数据库或对象存储不可用 |

## 14. 最小客户端流程

```text
1. 外部登录系统登录，获得 access_token
2. GET /api/user/skills?visibility=global
3. 用户选择 Skill
4. GET /api/user/skills/{id}/download
5. 直接下载返回的 url
```

上传者流程：

```text
1. POST /api/user/skills/upload
2. 得到 private Skill
3. POST /api/user/skills/{id}/publish
4. 等待租户管理员或平台管理员审核
5. 审核通过后进入对应市场
```
