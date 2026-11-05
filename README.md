# NOTE4 墨屏信息台

面向 ZECTRIX Note 4 的局域网信息屏。应用把天气、相册、自选行情、板块、新闻、黄金和持仓页渲染为 400×300 图片，通过官方本地推送 SDK 发送到设备。

控制台是一个可在电脑或手机浏览器打开的网页，可设置天气地点、上传照片、选择股票代码和配置设备地址。运行数据保存在本机，不依赖云端推送服务。

## 功能

- 天气：按城市名称查询坐标，使用 Open-Meteo 获取逐小时和七日预报。
- 相册：网页上传照片，或读取兼容的电子相框服务、挂载的只读照片目录。
- 行情：搜索、添加、排序和删除沪深股票、ETF、指数；支持 1、5、15 分钟更新。
- 页面：天气、相册、行情、板块、新闻、黄金、持仓和应用切换器。
- 灰阶：天气与相册使用 16 阶灰；板块与切换器使用黑白；行情可选四阶灰或黑白。
- 隐私：没有个人照片、设备 ID、局域网地址、运行缓存或凭据进入仓库。

## 环境要求

- ZECTRIX Note 4，推荐固件 3.7.5。
- 运行主机与设备处于同一局域网。
- Python 3.11 或更新版本。
- 本机运行需要 Rust stable、C/C++ 编译器和 CMake；Docker 方式会自行构建。
- 设备型号为 `zectrix-s3-epaper-4.2`，完整画布为 400×300。

## 本机启动

```sh
git clone https://github.com/wuyinyuanye/note4-eink-dashboard.git
cd note4-eink-dashboard
python3 -m venv .venv
.venv/bin/pip install -r local_app/requirements.txt
python3 scripts/fetch-font.py
CMAKE_POLICY_VERSION_MINIMUM=3.5 cargo build --locked --manifest-path local_app/bridge/Cargo.toml
.venv/bin/python local_app/app.py --data-dir data
```

启动日志会输出控制台地址（例如 `http://192.0.2.10:18772/`，实际值以日志为准）。在同一局域网的浏览器中打开它，首次使用时：

1. 在“设备连接”中填写 Note 4 的 IPv4 地址和设备 ID，保存后连接。
2. 在“天气地点”中搜索城市并保存，或直接填写经纬度。
3. 在“照片库”上传横屏照片。
4. 在“行情自选”中搜索代码，确认名称后添加。

设备 ID 可从设备信息页或官方 SDK 工具读取。源码只包含 RFC 5737 示例地址，不连接任何真实设备。

macOS 也可双击 `local_app/start.command`。脚本会建立虚拟环境、安装依赖、获取官方字体并构建桥，随后打开控制台。首次配置前不会自动连接设备。

## 配置

网页修改的天气地点和设备地址会原子写入数据目录中的 `config.json`，文件权限为 `0600`。命令行指定的 `--config` 和 `--data-dir` 优先于默认值。

常用环境变量：

| 变量 | 作用 |
| --- | --- |
| `NOTE4_DATA_DIR` | 持久数据目录，默认位于用户应用数据目录 |
| `NOTE4_BRIDGE` | 编译后的桥可执行文件路径 |
| `NOTE4_PHOTO_INDEX` | 可选的只读照片索引 JSON |
| `NOTE4_PHOTO_ROOT` | 可选的只读照片根目录 |
| `NOTE4_CLOCK_TEXT_FONT` | 中文渲染字体；未设置时使用 `fonts/zfull_GB.ttf` 或系统字体 |
| `NOTE4_CLOCK_DIGIT_FONT` | 数字渲染字体；未设置时使用系统字体 |

照片目录模式需要两个变量同时设置。索引格式为：

```json
{
  "photos": [
    {"id": "photo-001", "path": "2026/trip.jpg", "w": 2400, "h": 1600}
  ]
}
```

服务模式兼容 `GET /api/photos?limit=12&offset=0` 和 `GET /api/photo/{id}`，无需安装特定相册软件。

## Docker

仓库提供了 Linux/NAS 的 Docker 构建文件。先在仓库根目录创建 `.env`：

```sh
NOTE4_AUTOCONNECT=0
NOTE4_PAGE=home
```

然后运行：

```sh
docker compose -f local_app/deploy/nas/compose.yaml up -d --build
```

控制台监听主机的 `18772` 端口。确认天气、照片和股票设置后，把 `NOTE4_AUTOCONNECT` 改为 `1` 并重启容器。容器使用 host 网络以访问设备的 UDP 32342 端口。

## 测试

```sh
python3 scripts/fetch-font.py
cd local_app
../.venv/bin/python -m unittest discover -s . -p 'test_*.py'
node --test test_control.cjs
CMAKE_POLICY_VERSION_MINIMUM=3.5 cargo test --locked --manifest-path bridge/Cargo.toml
```

天气、行情、黄金和新闻来自公开网络服务。服务异常或数据过期时，界面会保留上一次有效内容并标注状态。SDK 的“显示完成”回执表示设备接收并完成显示调用，实际刷新波形仍由固件和屏幕表现决定。

## 安全边界

控制台只应在可信局域网内运行，没有公网身份认证。服务拒绝非法 Host、跨站 Origin 和跨站写入请求；上传和 JSON 请求均有大小限制。若需公网访问，请在可信反向代理之后增加身份认证和 TLS，不要直接暴露端口。

## 许可

本项目代码以 MIT License 发布。ZECTRIX SDK 保留其 MIT 许可；官方中文字体按 `assets` 说明单独获取，不随仓库再分发。详见 [NOTICE.md](NOTICE.md)。
