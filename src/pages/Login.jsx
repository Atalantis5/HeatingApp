/**
 * Login.jsx — V8.3 登录/注册页
 *
 * 视觉：工程蓝 + 深蓝品牌侧栏 + 白色登录卡片，与 V8.2 设计系统保持一致。
 * 交互：POST /api/token（OAuth2 表单）→ 保存 JWT → onLogin 交给 App 路由守卫；
 *       POST /api/users/register 用于首次测试注册。
 * 本页仅处理认证，不包含任何水力计算逻辑。
 */
import React, { useState } from "react";
import { Alert, Button, Card, Col, Form, Input, Row, Space, Tabs, Tag, Typography } from "antd";
import {
  ApartmentOutlined,
  LockOutlined,
  SafetyCertificateOutlined,
  UserOutlined,
} from "@ant-design/icons";

import { setAuthToken } from "../utils/request";

const API_BASE = "/api";

function readableError(data, fallback) {
  const detail = data && (data.detail || data.message);
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail)) return detail.map((item) => item.msg || JSON.stringify(item)).join("；");
  return fallback;
}

export default function Login({ onLogin }) {
  const [mode, setMode] = useState("login");
  const [loading, setLoading] = useState(null); // "login" | "register" | null
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loginForm] = Form.useForm();

  const handleLogin = async (values) => {
    setError("");
    setNotice("");
    setLoading("login");
    try {
      const body = new URLSearchParams();
      body.set("username", values.username.trim());
      body.set("password", values.password);
      const response = await fetch(`${API_BASE}/token`, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: body.toString(),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(readableError(data, `登录失败（HTTP ${response.status}）`));
      }
      if (!data.access_token) throw new Error("登录响应缺少 access_token");
      setAuthToken(data.access_token);
      onLogin?.(data.access_token);
    } catch (loginError) {
      setError(loginError.message || "登录失败，请稍后重试");
    } finally {
      setLoading(null);
    }
  };

  const handleRegister = async (values) => {
    setError("");
    setNotice("");
    setLoading("register");
    try {
      const response = await fetch(`${API_BASE}/users/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: values.username.trim(),
          password: values.password,
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(readableError(data, `注册失败（HTTP ${response.status}）`));
      }
      setNotice(`账号「${data.username}」注册成功，请使用新账号登录`);
      setMode("login");
      loginForm.setFieldsValue({ username: data.username, password: "" });
    } catch (registerError) {
      setError(registerError.message || "注册失败，请稍后重试");
    } finally {
      setLoading(null);
    }
  };

  return (
    <div
      data-testid="login-page"
      style={{
        minHeight: "100vh",
        display: "flex",
        background: "linear-gradient(135deg, #071426 0%, #0f2a52 52%, #2f54eb 140%)",
      }}
    >
      {/* 品牌侧 */}
      <Row style={{ width: "100%", minHeight: "100vh" }} align="stretch">
        <Col xs={0} md={10} lg={11}>
          <div
            style={{
              minHeight: "100vh",
              padding: "52px 56px",
              display: "flex",
              flexDirection: "column",
              justifyContent: "space-between",
              color: "#e2e8f0",
            }}
          >
            <div>
              <div
                style={{
                  width: 54,
                  height: 54,
                  borderRadius: 14,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontSize: 26,
                  color: "#fff",
                  background: "linear-gradient(135deg, #2f54eb, #1890ff)",
                  boxShadow: "0 10px 28px rgba(47,84,235,0.45)",
                  marginBottom: 26,
                }}
              >
                <ApartmentOutlined />
              </div>
              <Typography.Title level={2} style={{ color: "#fff", margin: 0, letterSpacing: 1 }}>
                陈致远 暖通计算工作台
              </Typography.Title>
              <div
                style={{
                  color: "rgba(191, 210, 255, 0.7)",
                  fontSize: 12,
                  letterSpacing: "0.18em",
                  marginTop: 8,
                  textTransform: "uppercase",
                }}
              >
                HeatingApp_CZY · Hydraulic Workbench
              </div>
              <div style={{ marginTop: 28, maxWidth: 430, color: "rgba(226,232,240,0.72)", lineHeight: 1.9 }}>
                重力循环双管热水供暖管网水力计算系统
                <br />
                NetworkX 拓扑分析 · 自动寻优 · Hardy-Cross 平差
              </div>
              <Space size={8} wrap style={{ marginTop: 22 }}>
                <Tag color="blue">PBKDF2 密码哈希</Tag>
                <Tag color="blue">JWT 鉴权</Tag>
                <Tag color="blue">多租户数据隔离</Tag>
              </Space>
            </div>
            <div style={{ color: "rgba(226,232,240,0.45)", fontSize: 12 }}>
              <SafetyCertificateOutlined style={{ marginRight: 6 }} />
              登录后仅可访问当前账号名下的工程案例数据
            </div>
          </div>
        </Col>

        {/* 表单侧 */}
        <Col xs={24} md={14} lg={13}>
          <div
            style={{
              minHeight: "100vh",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              padding: "32px 18px",
            }}
          >
            <Card
              style={{
                width: "100%",
                maxWidth: 430,
                borderRadius: 14,
                border: "1px solid rgba(255,255,255,0.65)",
                boxShadow: "0 24px 70px rgba(2, 12, 32, 0.35)",
              }}
              styles={{ body: { padding: "26px 28px 22px" } }}
            >
              <div style={{ textAlign: "center", marginBottom: 8 }}>
                <div style={{ fontSize: 12, color: "#94a3b8", letterSpacing: "0.12em" }}>
                  HYDRAULIC WORKBENCH
                </div>
                <Typography.Title level={3} style={{ margin: "8px 0 4px", color: "#0f172a" }}>
                  管网计算工作台
                </Typography.Title>
                <Typography.Text type="secondary">请登录后开始计算与案例管理</Typography.Text>
              </div>

              {notice ? (
                <Alert
                  type="success"
                  showIcon
                  message={notice}
                  style={{ margin: "14px 0 0" }}
                  data-testid="login-notice"
                />
              ) : null}
              {error ? (
                <Alert
                  type="error"
                  showIcon
                  message={error}
                  style={{ margin: "14px 0 0" }}
                  data-testid="login-error"
                />
              ) : null}

              <Tabs
                activeKey={mode}
                onChange={(key) => {
                  setMode(key);
                  setError("");
                  setNotice("");
                }}
                style={{ marginTop: 10 }}
                items={[
                  {
                    key: "login",
                    label: "账号登录",
                    children: (
                      <Form form={loginForm} layout="vertical" onFinish={handleLogin} requiredMark={false}>
                        <Form.Item
                          name="username"
                          label="用户名"
                          rules={[{ required: true, message: "请输入用户名" }]}
                        >
                          <Input
                            id="login-username"
                            data-testid="login-username"
                            size="large"
                            prefix={<UserOutlined style={{ color: "#94a3b8" }} />}
                            placeholder="请输入用户名"
                            autoComplete="username"
                          />
                        </Form.Item>
                        <Form.Item
                          name="password"
                          label="密码"
                          rules={[{ required: true, message: "请输入密码" }]}
                        >
                          <Input.Password
                            id="login-password"
                            data-testid="login-password"
                            size="large"
                            prefix={<LockOutlined style={{ color: "#94a3b8" }} />}
                            placeholder="请输入密码"
                            autoComplete="current-password"
                          />
                        </Form.Item>
                        <div
                          data-testid="login-copyright"
                          style={{
                            color: "#94a3b8",
                            fontSize: 12,
                            textAlign: "center",
                            lineHeight: 1.6,
                            margin: "-2px 0 14px",
                          }}
                        >
                          © 2026 陈致远 版权所有
                        </div>
                        <Button
                          data-testid="btn-login"
                          type="primary"
                          htmlType="submit"
                          size="large"
                          block
                          loading={loading === "login"}
                        >
                          登 录
                        </Button>
                      </Form>
                    ),
                  },
                  {
                    key: "register",
                    label: "注册账号",
                    children: (
                      <Form layout="vertical" onFinish={handleRegister} requiredMark={false}>
                        <Form.Item
                          name="username"
                          label="用户名"
                          rules={[
                            { required: true, message: "请输入用户名" },
                            { min: 3, max: 64, message: "用户名长度 3-64 位" },
                            {
                              pattern: /^[A-Za-z0-9_.-]+$/,
                              message: "仅支持字母/数字/下划线/点/横线",
                            },
                          ]}
                        >
                          <Input
                            id="register-username"
                            data-testid="register-username"
                            size="large"
                            prefix={<UserOutlined style={{ color: "#94a3b8" }} />}
                            placeholder="3-64 位字母/数字"
                            autoComplete="new-username"
                          />
                        </Form.Item>
                        <Form.Item
                          name="password"
                          label="密码"
                          rules={[
                            { required: true, message: "请输入密码" },
                            { min: 6, max: 128, message: "密码至少 6 位" },
                          ]}
                        >
                          <Input.Password
                            id="register-password"
                            data-testid="register-password"
                            size="large"
                            prefix={<LockOutlined style={{ color: "#94a3b8" }} />}
                            placeholder="至少 6 位密码"
                            autoComplete="new-password"
                          />
                        </Form.Item>
                        <Form.Item
                          name="confirm"
                          label="确认密码"
                          dependencies={["password"]}
                          rules={[
                            { required: true, message: "请再次输入密码" },
                            ({ getFieldValue }) => ({
                              validator(_, value) {
                                if (!value || getFieldValue("password") === value) {
                                  return Promise.resolve();
                                }
                                return Promise.reject(new Error("两次输入的密码不一致"));
                              },
                            }),
                          ]}
                        >
                          <Input.Password
                            id="register-confirm"
                            data-testid="register-confirm"
                            size="large"
                            prefix={<LockOutlined style={{ color: "#94a3b8" }} />}
                            placeholder="再次输入密码"
                            autoComplete="new-password"
                          />
                        </Form.Item>
                        <Button
                          data-testid="btn-register"
                          type="primary"
                          htmlType="submit"
                          size="large"
                          block
                          loading={loading === "register"}
                        >
                          注 册
                        </Button>
                      </Form>
                    ),
                  },
                ]}
              />

              <div style={{ textAlign: "center", marginTop: 12, color: "#94a3b8", fontSize: 11 }}>
                初始管理员由部署方通过环境变量配置，系统不再提供默认密码
              </div>
            </Card>
          </div>
        </Col>
      </Row>
    </div>
  );
}
