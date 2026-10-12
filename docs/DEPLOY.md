# 部署到 Streamlit Community Cloud（已完成准备）

仓库：https://github.com/wang-yan2025/FinSight （公开，公开内容已核验不含密钥/缓存/日志）

## 网页操作步骤（约 3 分钟）

1. 打开 **https://share.streamlit.io/**，用 GitHub 账号登录（与仓库同账号 wang-yan2025）
2. 点 **Create app** → **Deploy a public app from GitHub**
3. 填写：
   - Repository：`wang-yan2025/FinSight`
   - Branch：`master`
   - Main file path：`app.py`
4. （可选 LLM 增强）点 **Advanced settings → Secrets**，粘贴以下内容
   （如需配置，把实际值从本机 `.env` 复制过去；不配置则自动降级为基础备忘录模式，功能完整）：

   ```toml
   FINSIGHT_LLM_API_KEY = "（见本机 .env，勿提交仓库）"
   FINSIGHT_LLM_BASE_URL = "https://api.deepseek.com/v1"
   FINSIGHT_LLM_MODEL = "deepseek-chat"
   ```

5. 点 **Deploy**，约 2-4 分钟构建完成后获得公开地址（形如 https://finsight-xxxx.streamlit.app）

## 部署后行为说明

- 首次查询某只股票时需联网拉取报表（约 10-30 秒），之后 12 小时走内存缓存
- 云端无本地缓存文件属正常；来源标注会显示"接口取数"
- 执行日志落在云端容器的 `data/logs/`（容器重启即消失，属预期）
- 修改代码后：`git push origin master` 即可自动重新部署

## 国内访问提示

Streamlit Cloud（*.streamlit.app）在国内普通网络下访问可能不稳定；
若团队成员无法访问，改用 Zeabur（支持私有仓库）或内网穿透方案，见此前方案对比。
