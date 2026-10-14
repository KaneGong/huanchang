# 幻场：GitHub → Vercel → Supabase

更新：2026-10-14。GitHub源码已公开发布；Supabase测试项目、18张私有业务表和私有图片桶已创建。Vercel项目已连接仓库，正在配置测试环境；尚未完成公网部署验收或迁移本机用户数据。

## 架构

保留原生前端与既有账号名、密码、选填邮箱方式。Flask/WSGI适配器复用现有服务端规则；Supabase只承担PostgreSQL与私有图片存储，不在这一轮替换成Supabase Auth或重做前端。

`cloud_app.py`是Vercel入口；`cloud/database.py`使用事务池兼容设置和每实例最多2个连接，关闭prepared statements。事务内的账户级限流复用该事务，避免并发请求占满连接后再等待额外连接。IP限流使用共享固定分钟窗口，正式Vercel模式读取平台覆盖的客户端地址头；普通自部署不相信用户提交的转发头。私有`huanchang` schema不进入Data API，RLS无浏览器策略，撤销PUBLIC/anon/authenticated权限。首版用事务级写锁保持原SQLite串行写入下的配额、修订及状态检查语义；大规模写入需进一步拆分锁并专项并发验收。

私有`huanchang-media`图片桶：发布前验证是否属于账户；授权直传，完成后检查实际大小与文件头，登记后才可加入作品。下载沿用原服务端可见性校验，再生成60秒访问地址。不要设置公共桶，也不要允许浏览器直接读取数据库或拿到服务密钥。未完成上传的对象保留为孤立对象，正式运行需增加经过审阅的存储清理任务；当前未创建删除任务。

`scripts/build_vercel.py`将批准的前端复制到`public/`供CDN服务；排除seed、QA及本机用户数据。静态CSP允许Supabase项目域名；若使用自定义存储域名，应缩小/调整vercel.json的对应来源。邮件在云端请求结束前完成投递尝试，不依赖响应后的进程线程；仍需实际SMTP验收及失败重试运营策略。

## 创建项目后的步骤

1. 在Supabase选择区域，创建项目；使用新环境执行`cloud/schema.sql`。数据库地址使用控制台Connect给出的事务池地址，开启TLS。不要凭区域名称猜地址。
2. 设置私有图片桶，允许JPG/PNG/WebP，桶单文件上限20 MB。服务器密钥仅保存在Vercel的私有环境设置中。
3. 按`.env.cloud.example`填环境变量；数据库地址可以不包含密码，将密码单独填入Secret变量`HUANCHANG_DATABASE_PASSWORD`，服务端自动编码特殊符号。也兼容原有包含密码的完整URI。Preview与Production使用独立数据与图片桶。预览站点来源使用Vercel提供的VERCEL_URL；生产站点使用明确的HUANCHANG_PUBLIC_ORIGIN。Cookie为Secure/HttpOnly/__Host前缀，示例登录关闭。
4. 连接GitHub发布仓库。识别Flask框架和pyproject入口，构建执行`python scripts/build_vercel.py`。先部署Preview，完整验证后再发布Production。
5. 决定正式站从空数据开始，还是导入指定的本机试用内容。先执行`scripts/cloud_data_plan.py`进行只读盘点；账户、私信、邮箱和图片转移范围需明确，不自动上传现有本机数据。历史会话与验证码不迁移。
6. 注册运营账户，在私有数据库中明确指定管理员；没有预设管理员密码或公开初始化接口。

可选的展示初始化：`python scripts/cloud_seed_demo.py`只显示计划；选择项目后用`--apply`添加仓库自带且标为“示例”的内容，不读取本机用户数据或上传目录。示例作者标为不可合作、不进入真实创作者发现，不能登录或接收真实私信。此步骤不是导入用户试用内容；本轮未对任何云项目执行。

缺少配置时返回503，不会自动退回SQLite或示例网站。不要因为静态首页已显示就宣称完整可用。

## 上线验收

- 两个独立浏览器注册、登录、编辑主页、退出/重新登录；选填邮箱保持可选。
- 20 MB以内图片直传、顺序、裁切、编辑、私有草稿与发布后展示；其他人不能完成你的上传或读取未发布的图片。
- 搜索与分页、评论回复、点赞收藏、个人统计、私信接收/回复/历史、屏蔽、举报与管理。
- 邮件真实到达、验证、验证码失效、密码找回与旧会话撤销。
- 函数重启后数据与会话持续；限流在不同实例间生效；Vercel实际客户端地址头、CSP、签名存储URL及上传CORS正确。
- 生产与预览隔离；数据库/存储备份及目标环境恢复演练。
- 明确运营主体、安全联系、保留期限、删除/导出处理与举报负责人。

## 国内浏览体验试验

先使用实际Vercel访问地址，不先购买域名。通过真实国内网络（家用宽带、移动网络，尽可能覆盖不同运营商）分别检查首次打开、连续浏览、图片、登录、上传、消息与邮件找回；记录失败、加载时间与用户感受。不要用单次电脑访问或国外测速代替用户体验。

若页面或图片不稳定，先区分Vercel页面/接口、Supabase图片/数据库与邮件各自问题。只迁移页面不一定改善Supabase链路。再决定是否迁移到用户已有阿里云资源，保留代码与数据可迁移的边界；不在本轮购买域名、实例或创建云资源。

当前Vercel账户是Hobby。正式运营用途需要核对官方非商业限制；网站免费并不自动满足Hobby条件。不自动购买或升级套餐。

## 官方依据（2026-10-14核对）

- [Vercel Flask入口、构建与public静态目录](https://vercel.com/docs/frameworks/backend/flask)
- [Vercel提供的客户端地址头](https://vercel.com/docs/headers/request-headers)
- [Supabase事务池连接、TLS与prepared statements](https://supabase.com/docs/guides/database/connecting-to-postgres)
- [Supabase签名上传](https://supabase.com/docs/reference/python/storage-from-createsigneduploadurl)
- [Vercel Hobby使用边界](https://vercel.com/docs/limits/fair-use-guidelines)
