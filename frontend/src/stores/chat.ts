import { defineStore } from "pinia";
import { ref, reactive } from "vue";
import {
  getSessions,
  createSession as createSessionApi,
  deleteSession as deleteSessionApi,
  renameSession as renameSessionApi,
  getSessionMessages,
  type Session,
} from "@/api/sessions";
import {
  sendMessage as sendMessageSse,
  approveSession,
  type Message,
  type ChatEvent,
  type ApprovalPayload,
} from "@/api/chat";

/**
 * 聊天状态 Store
 * 管理会话列表、当前会话、消息流, 以及 SSE 流式接收与审批拦截
 */
export const useChatStore = defineStore("chat", () => {
  const sessions = ref<Session[]>([]);
  const currentSessionId = ref<string | null>(null);
  const messages = ref<Message[]>([]);

  const loadingSessions = ref(false);
  const loadingMessages = ref(false);
  const streaming = ref(false);

  /** 执行链路: 记录当前请求经过的节点, 用于前端展示进度 */
  const nodeSteps = ref<{ node: string; label: string }[]>([]);

  // 命中审批拦截时携带的负载, 非空则由 Chat.vue 弹出审批弹窗
  const pendingApproval = ref<ApprovalPayload | null>(null);

  /** 当前会话对象 */
  const currentSession = ref<Session | null>(null);

  /** 生成临时唯一 id */
  function tempId(): string {
    return `tmp_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
  }

  /** 从图片 URL 推导文件名(兼容 /uploads/x.png 与 /api/files?path=...) */
  function filenameFromUrl(url: string): string {
    try {
      const u = new URL(url, window.location.origin);
      if (u.pathname === "/api/files" && u.searchParams.has("path")) {
        const p = decodeURIComponent(u.searchParams.get("path") || "");
        return p.split(/[\\/]/).filter(Boolean).pop() || "图";
      }
      return decodeURIComponent(u.pathname.split("/").filter(Boolean).pop() || "图");
    } catch {
      return "图";
    }
  }

  /**
   * 从 API 加载会话列表
   */
  async function loadSessions(): Promise<void> {
    loadingSessions.value = true;
    try {
      sessions.value = await getSessions();
    } finally {
      loadingSessions.value = false;
    }
  }

  /**
   * 新建会话并选中之
   */
  async function createSession(title?: string): Promise<Session> {
    const session = await createSessionApi({ title });
    sessions.value.unshift(session);
    await selectSession(session.id);
    return session;
  }

  /**
   * 切换会话: 设置当前会话并加载其历史消息
   */
  async function selectSession(id: string): Promise<void> {
    if (currentSessionId.value === id) return;
    currentSessionId.value = id;
    currentSession.value = sessions.value.find((s) => s.id === id) || null;
    pendingApproval.value = null;
    await loadMessages(id);
  }

  /**
   * 加载某个会话的历史消息
   */
  async function loadMessages(id: string): Promise<void> {
    loadingMessages.value = true;
    messages.value = [];
    try {
      const history = await getSessionMessages(id);
      // 方案B: 历史 AI 消息优先用后端返回的 evidence(含文本来源+引用索引, 可跳转);
      // 老消息没有则用 images(持久化的证据图 URL)回退还原为图片证据
      messages.value = history.map((m) => {
        if (m.role === "ai") {
          if (m.evidence?.length) return m;
          if (m.images?.length) {
            return {
              ...m,
              evidence: m.images.map((url) => ({ url, filename: filenameFromUrl(url), type: "image" })),
            };
          }
        }
        return m;
      });
    } catch {
      messages.value = [];
    } finally {
      loadingMessages.value = false;
    }
  }

  /**
   * 删除会话
   * 删除后若当前会话被删除, 自动选中第一个剩余会话;
   * 若无剩余会话则 currentSessionId 置空, 由调用方决定是否新建
   */
  async function removeSession(id: string): Promise<void> {
    await deleteSessionApi(id);
    sessions.value = sessions.value.filter((s) => s.id !== id);
    if (currentSessionId.value === id) {
      currentSessionId.value = null;
      currentSession.value = null;
      messages.value = [];
      pendingApproval.value = null;
      // 如果还有其他会话,自动选中第一个
      if (sessions.value.length > 0) {
        await selectSession(sessions.value[0].id);
      }
    }
  }

  /**
   * 重命名会话
   */
  async function renameSession(id: string, title: string): Promise<void> {
    const updated = await renameSessionApi(id, title);
    const session = sessions.value.find((s) => s.id === id);
    if (session) session.title = updated.title;
    if (currentSession.value?.id === id)
      currentSession.value.title = updated.title;
  }

  /**
   * 发送消息: 立即插入用户消息, 创建 AI 占位消息, 通过 SSE 流式接收回复
   */
  async function sendMessage(
    text: string,
    image: string | null,
  ): Promise<void> {
    // 无当前会话或会话已不在本地列表(可能已删除)时, 拒绝发送避免 404
    if (!currentSessionId.value || streaming.value) return;
    const sessionExists = sessions.value.some(
      (s) => s.id === currentSessionId.value,
    );
    if (!sessionExists) {
      currentSessionId.value = null;
      currentSession.value = null;
      return;
    }

    // 插入用户消息
    const userImages = image ? [image] : undefined;
    messages.value.push({
      id: tempId(),
      role: "human",
      content: text,
      images: userImages,
    });

    // AI 占位消息, 后续按 token 追加实现打字效果
    // 必须用 reactive 包装: 否则 push 到 ref 数组后, aiMessage 变量仍指向原始对象,
    // 直接修改 aiMessage.content 不会触发 Vue 的 Proxy set trap, 导致打字机效果失效
    const aiMessage = reactive<Message>({
      id: tempId(),
      role: "ai",
      content: "",
    });
    messages.value.push(aiMessage);

    // 清空执行链路
    nodeSteps.value = [];
    streaming.value = true;
    try {
      await sendMessageSse(
        currentSessionId.value,
        text,
        image,
        (evt: ChatEvent) => {
          handleEvent(evt, aiMessage);
        },
      );
    } catch (err) {
      aiMessage.content += `\n\n> ⚠️ 请求失败: ${err instanceof Error ? err.message : String(err)}`;
    } finally {
      streaming.value = false;
    }
  }

  /** 处理单条 SSE 事件 */
  function handleEvent(evt: ChatEvent, aiMessage: Message): void {
    const type = evt.event || evt.type;
    switch (type) {
      case "token": {
        // 流式 token, 追加到 AI 消息(打字效果)
        if (typeof evt.content === "string") {
          aiMessage.content += evt.content;
        }
        break;
      }
      case "node_update": {
        // 节点完成: 记录到执行链路
        if (evt.node && evt.label) {
          nodeSteps.value.push({ node: evt.node, label: evt.label });
        }
        break;
      }
      case "done": {
        // 结束事件: 仅当未收到任何 token 时用完整文本兜底,
        // 避免已有流式内容被覆盖导致视觉闪烁
        if (
          typeof evt.text === "string" &&
          evt.text &&
          !aiMessage.content
        ) {
          aiMessage.content = evt.text;
        }
        // 附带评估置信分(0-100), 前端渲染"置信章"
        if (typeof evt.score === "number") {
          aiMessage.score = evt.score;
        }
        // 方案B: 引用证据(被引用的知识库图片)
        if (Array.isArray(evt.evidence)) {
          aiMessage.evidence = evt.evidence;
        }
        break;
      }
      case "title_update": {
        // 后端自动生成的会话标题,更新本地会话列表
        if (typeof evt.title === "string" && evt.title) {
          const session = sessions.value.find(
            (s) => s.id === currentSessionId.value,
          );
          if (session) session.title = evt.title;
          if (currentSession.value) currentSession.value.title = evt.title;
        }
        break;
      }
      case "interrupt": {
        // 命中审批拦截, 暂停并弹出审批
        if (evt.approval) {
          // 把草稿答案写入 AI 消息(兜底: token 流可能未生效)
          if (typeof evt.approval.draft === "string" && evt.approval.draft) {
            aiMessage.content = evt.approval.draft;
          }
          pendingApproval.value = {
            ...evt.approval,
            session_id:
              evt.approval.session_id || currentSessionId.value || undefined,
          };
        }
        break;
      }
      case "error": {
        aiMessage.content += `\n\n> ⚠️ ${evt.message || "未知错误"}`;
        break;
      }
      default:
        // 其它事件暂不处理
        break;
    }
  }

  /** 关闭审批弹窗 */
  function clearApproval(): void {
    pendingApproval.value = null;
  }

  /**
   * 提交审批结果: POST /sessions/:id/approve
   * approve: 后端发 done 事件(含草稿答案),写入 AI 消息
   * reject: 后端发 token 流 + done(第四节点重新生成),实时追加
   */
  async function submitApproval(
    approved: boolean,
    reason?: string,
  ): Promise<void> {
    if (!currentSessionId.value) return;

    // 找到当前最后一条 AI 消息,审批结果写入它
    const lastAi = [...messages.value].reverse().find((m) => m.role === "ai");
    if (!lastAi) return;

    // reject 时清空旧草稿,重新流式输出
    if (!approved) {
      lastAi.content = "";
    }

    streaming.value = true;
    try {
      await approveSession(
        currentSessionId.value,
        { approved, reason },
        (evt: ChatEvent) => handleEvent(evt, lastAi),
      );
    } catch (err) {
      lastAi.content += `\n\n> ⚠️ 审批请求失败: ${err instanceof Error ? err.message : String(err)}`;
    } finally {
      streaming.value = false;
      clearApproval();
    }
  }

  return {
    sessions,
    currentSessionId,
    currentSession,
    messages,
    loadingSessions,
    loadingMessages,
    streaming,
    nodeSteps,
    pendingApproval,
    loadSessions,
    createSession,
    selectSession,
    removeSession,
    renameSession,
    loadMessages,
    sendMessage,
    clearApproval,
    submitApproval,
  };
});
