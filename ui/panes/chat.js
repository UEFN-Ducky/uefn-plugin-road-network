import { h, icon } from "../controls.js";
import { $, CHANNEL, call, rpc, savePrefs, showError, state } from "../core.js";
import { note } from "./tools.js";
import { resize } from "../view/camera.js";

// ------------------------------------------------------------------ chat: Ducky's real chat in a card over the map
// chat.open place "float" shows the chat itself (messages, composer, styles), never a copy, in a card that pops in
// above the round agent button; its header picker (choices) switches which agent's chat shows
const OWN_CHAT = "own";
let dockOpen = false;
const chatChoices = () => [...(state.view.ownChat ? [{ id: state.view.ownChat, title: "City Builder" }] : []),
  ...(note.chats || []).filter((c) => c.id !== state.view.ownChat).map((c) => ({ id: c.id, title: c.title }))];
function setDockState(s) {
  dockOpen = !!(s && s.open);
  const b = $("chatToggle");
  if (b) {
    b.setAttribute("aria-expanded", String(dockOpen));
    b.setAttribute("aria-label", dockOpen ? "Close the chat" : "Chat with an agent");
    b.title = dockOpen ? "Close the chat" : "Chat with an agent";
  }
  // the header picker swapped the chat: open that one next time
  if (s?.conv_id && dockOpen) {
    const pick = s.conv_id === state.view.ownChat ? OWN_CHAT : s.conv_id;
    if (pick !== (state.view.chat || OWN_CHAT)) { state.view.chat = pick; savePrefs(); }
  }
  if (s?.place && s.place !== "float") setTimeout(resize, 50);  // an older Ducky docks it beside the map
}
async function openChatDock(draft = "") {
  const pick = state.view.chat || OWN_CHAT;
  const world = state.nets.find((n) => n.id === state.net);
  const which = pick === OWN_CHAT
    ? { key: "main", title: "City Builder", brief: "The person is in City Builder (plugin road-network). Use the road-network "
        + `skill and the road_network_* tools. The open world is net_id '${state.net || ""}'${world?.name ? ` (${world.name})` : ""}.` }
    : { conv_id: pick, title: (note.chats || []).find((c) => c.id === pick)?.title || "Chat" };
  try {
    let s = await call("chat.open", { ...which, place: "float", expanded: true, size: 420, offset: 80, choices: chatChoices(),
      ...(draft ? { draft } : {}) });
    if (pick === OWN_CHAT && s?.conv_id && s.conv_id !== state.view.ownChat) {
      state.view.ownChat = s.conv_id;  // the plugin's own chat, so the picker can list it
      savePrefs();
      s = await call("chat.set", { choices: chatChoices() });
    }
    // a Ducky without the floating card docks it under the map: put it beside the map instead
    if (s && s.place !== "float") s = await call("chat.set", { place: "right", expanded: true, size: 420 });
    setDockState(s);
  } catch (e) {
    showError(e.message);
  }
}
async function closeChatDock() {
  setDockState(await call("chat.close").catch(() => null));
}
function renderChatBar() {
  // the launcher: a round agent button; its icon turns into a close cross while the chat is open
  const btn = h("button", { type: "button", class: "rn-launch", id: "chatToggle", "aria-expanded": "false",
    "aria-label": "Chat with an agent", title: "Chat with an agent" },
  h("span", { class: "rn-launch__icon rn-launch__icon--agent" }, icon("agent", 22)),
  h("span", { class: "rn-launch__icon rn-launch__icon--close" }, icon("x", 20)));
  btn.addEventListener("click", () => (dockOpen ? closeChatDock() : openChatDock()));
  $("chatBar").replaceChildren(btn);
  loadChats().then(() => { if (dockOpen) call("chat.set", { choices: chatChoices() }).catch(() => {}); });
  call("chat.state").then(setDockState).catch(() => {});
}
window.addEventListener("message", (ev) => {  // the card's own close button and chat picker report back here
  const e = ev.data?.channel === CHANNEL ? ev.data.event : null;
  if (e?.type === "chat.state") setDockState(e);
});


let chatsLoading = null;
function loadChats() {  // fetched once in the background (on start), refreshed after a send
  chatsLoading ||= rpc("chats").then((got) => { note.chats = got.ok === false ? [] : got.chats || []; chatsLoading = null; });
  return chatsLoading;
}


export { OWN_CHAT, dockOpen, chatChoices, setDockState, openChatDock, closeChatDock, renderChatBar, chatsLoading, loadChats };
