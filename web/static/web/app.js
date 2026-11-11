"use strict";
//网页的全部交互逻辑。结构：
//  1. 状态（state）：页面当前要显示的所有数据
//  2. 工具：建立页面元素、提示条、向服务器发请求
//  3. 读取数据：班级列表、班级信息、历史方案
//  4. 绘制：render()根据state重画整个页面，四个页面各有一个绘制函数
//  5. 操作：各个按钮点击后做的事
//页面不自己计算任何排座结果，所有数据都来自服务器；每次操作后用服务器返回的最新数据重画

// ---------- 1. 状态 ----------
const state = {
  classes: [],        //全部班级的名称
  name: null,         //当前班级的名称；没有班级时为null
  info: null,         //当前班级的信息（座位、学生、最近一次方案），来自服务器
  tab: "arrange",     //当前页面：arrange排座、layout座位表、roster名单、history历史、help帮助
  app: { desktop: false, version: "", data_dir: "" },   //程序信息：是否为桌面版、版本号、数据文件夹的位置
  preview: null,      //正在预览的方案{score, assignments}；没有预览时为null
  picked: null,       //预览中已点选、等待对调的座位"x,y"
  editor: null,       //座位表编辑器的内容{x0, y0, rows, cols, cells, dirty}
  draft: "",          //名单页输入框里还没有提交的文字
  history: null,      //历史页的数据{assignments, has_more}；还没有读取时为null
  historyLimit: 20,   //历史页读取的方案数量
  shown: null,        //历史页正在查看的方案编号
  //排座页的显示偏好，保存在这个浏览器里，下次打开仍然有效
  showBurden: loadPreference("showBurden"),    //是否显示各座位的负担值（数字、底色和图例），默认不显示
  showTable: loadPreference("showTable"),      //是否展开历史负担表格，默认折叠
  //排座页座位图的缩放和位置：scale是缩放比例，x、y是座位图左上角在显示区域中的位置（像素）
  //auto为true表示自动缩放到刚好完整显示；用户自己缩放或拖动之后变为false
  view: { scale: 1, x: 0, y: 0, auto: true },
};

//读取、保存显示偏好。浏览器不允许保存时（例如无痕模式的某些设置）用默认值，不影响使用
function loadPreference(name) {
  try {
    return localStorage.getItem("randomSeat." + name) === "1";
  } catch (error) {
    return false;
  }
}
function savePreference(name, on) {
  try {
    localStorage.setItem("randomSeat." + name, on ? "1" : "0");
  } catch (error) {
    //保存不了只是下次打开时回到默认值，这里不需要处理
  }
}

//座位坐标的最大值、每个班的座位数上限，与后端seat_random/config.py中的同名参数一致。
//这里只用于界面上的提示和限制，真正的检查在后端
const MAX_COORDINATE = 100;
const MAX_SEATS = 500;

const $ = (id) => document.getElementById(id);
const key = (x, y) => x + "," + y;                                        //座位坐标 -> 字典的键
const signed = (value) => (value >= 0 ? "+" : "") + value.toFixed(3);     //负担值的显示形式

// ---------- 2. 工具 ----------

//建立一个页面元素。attrs中以on开头的是事件处理函数，其余是属性；children是子元素或文字
//文字一律作为纯文本放入，不会被当成HTML执行
function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs || {})) {
    if (name === "class") node.className = value;
    else if (name.startsWith("on")) node.addEventListener(name.slice(2), value);
    else if (value === true) node.setAttribute(name, "");
    else if (value !== false && value != null) node.setAttribute(name, value);
  }
  for (const child of children.flat()) {
    if (child != null && child !== false) node.append(child);
  }
  return node;
}

//在页面底部显示几秒钟的提示；isError为true时用红色
let toastTimer = null;
function toast(message, isError) {
  const box = $("toast");
  box.textContent = message;
  box.className = "show" + (isError ? " error" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { box.className = ""; }, isError ? 6000 : 3000);
}

//弹出一个询问框，等用户回答。代替浏览器自带的输入框和确认框：样式与页面一致，说明文字也更清楚
//  options.input为true时带一个输入框：点"确定"得到输入的文字，点"取消"或按Esc得到null
//  否则是确认框：点"确定"得到true，点"取消"或按Esc得到false
//  options.value是输入框里预先填好的文字；options.okText是确定按钮上的字；options.danger为true时确定按钮用红色
//用法：const name = await ask("新班级的名称：", { input: true });
function ask(message, options) {
  options = options || {};
  return new Promise((resolve) => {
    const dialog = $("ask");
    const input = options.input ? el("input", { type: "text", id: "ask-input", maxlength: "60", autocomplete: "off" }) : null;
    if (input) input.value = options.value || "";
    const finish = (result) => {
      dialog.close();
      resolve(result);
    };
    const cancel = () => finish(input ? null : false);
    dialog.replaceChildren(el("form", { onsubmit: (event) => { event.preventDefault(); finish(input ? input.value : true); } },
      el("p", { class: "ask-message", id: "ask-message" }, message),
      input,
      el("div", { class: "toolbar" },
        el("button", { type: "button", id: "ask-cancel", onclick: cancel }, "取消"),
        el("button", { type: "submit", id: "ask-ok", class: "primary" + (options.danger ? " danger-fill" : "") },
          options.okText || "确定"))));
    dialog.oncancel = (event) => { event.preventDefault(); cancel(); };     //按Esc等同于取消
    dialog.showModal();
    if (input) {
      input.focus();
      input.select();
    }
  });
}

//后端的错误信息是英文，这里转成中文提示；没有对应条目的原样显示
const ERROR_TEXT = [
  [/^Unknown class: (.*)/, "班级不存在：$1"],
  [/^Class already exists: (.*)/, "班级已存在：$1"],
  [/^Unknown student: (.*)/, "学生不存在：$1"],
  [/^Student already exists: (.*)/, "学生已存在：$1"],
  [/^Student has history/, "该学生已有排座历史，不能删除，请改为标记离开"],
  [/^Name must not be empty/, "名称不能为空"],
  [/^Name must not contain/, "名称不能包含“/”，也不能是“.”或“..”"],
  [/^Name is too long/, "名称太长（最多50个字）"],
  [/^Name contains invisible or control characters/, "名称中有看不见的特殊字符（如换行、零宽字符），请重新输入"],
  [/^Class data has changed/, "数据已被其他操作修改（例如在另一个网页里排过座），已刷新为最新数据，请重新操作"],
  [/^Too many seats \(at most (\d+)\)/, "座位太多了（每个班最多$1个）"],
  [/^Too many students \(at most (\d+)\)/, "学生太多了（每个班在班学生最多$1人）"],
  [/^Seat position out of range: (.*)/, "座位坐标超出范围：$1"],
  [/^Corrupted (record|seat position).*/, "数据库中有损坏的记录，无法读取这个班级。请点击页面底部的“检查数据完整性”查看详情（$&）"],
  [/^Database is busy or unavailable.*/, "数据库正被其他程序占用或无法读写，请稍后再试"],
  [/^Internal server error/, "服务器内部出错，详细信息已记录在运行服务的窗口中"],
  [/^Request is too large/, "一次提交的内容太多了"],
  [/^limit must be between/, "一次读取的记录数量超出范围"],
  [/^Only available in the desktop app/, "这个功能只在桌面版程序中可用"],
  [/^Cannot open.*/, "无法打开：文件或文件夹不存在"],
  [/^Not enough seats/, "座位数少于在班学生数，无法排座"],
  [/^Classroom has no seats/, "还没有设置座位表"],
  [/^Class has no students/, "班级里还没有在班的学生"],
  [/^No assignment to roll back/, "没有可以撤销的排座记录"],
  [/^Duplicate seat positions/, "座位表中有重复的座位"],
  [/^Plan must contain every active student/, "方案与当前名单不一致，请重新生成"],
  [/^Seat not in class layout/, "方案中的座位已不在座位表中，请重新生成"],
  [/^A seat cannot hold multiple students/, "方案中有两个学生在同一个座位"],
];
function translate(message) {
  for (const [pattern, text] of ERROR_TEXT) {
    if (pattern.test(message)) return message.replace(pattern, text);
  }
  return message;
}

//向服务器发请求，返回解析后的结果；服务器拒绝时抛出带中文说明的错误
//修改数据的请求必须带上防伪令牌，令牌在打开网页时由服务器放进了cookie
//以这些文字开头的错误说明页面上的数据已经过期（别处改过数据），需要重新读取班级信息
const STALE_ERRORS = /^(Class data has changed|Plan must contain every active student|Seat not in class layout|Unknown student)/;

async function api(method, path, body) {
  const tokenMatch = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
  let response;
  try {
    response = await fetch("/api/" + path, {
      method,
      headers: { "Content-Type": "application/json", "X-CSRFToken": tokenMatch ? tokenMatch[1] : "" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (error) {
    //请求根本没有发到服务器：服务没有启动、已经停止，或网络不通
    throw new Error("无法连接服务，请确认服务正在运行（python manage.py runserver）");
  }
  let data;
  try {
    data = await response.json();
  } catch (error) {
    //服务器返回的不是JSON。403通常是防伪令牌失效（网页开了太久，或浏览器禁用了cookie）
    if (response.status === 403) throw new Error("页面已过期，请刷新网页后重试");
    throw new Error("服务器没有返回可识别的内容（状态码" + response.status + "）");
  }
  if (!response.ok) {
    const message = String(data.error || response.status);
    const error = new Error(translate(message));
    error.stale = STALE_ERRORS.test(message);
    throw error;
  }
  return data;
}

//当前班级的接口路径。班级名称和学生编号要先编码，才能安全地放进网址
const classPath = (rest) => "classes/" + encodeURIComponent(state.name) + (rest || "");
const studentPath = (id, rest) => classPath("/students/" + encodeURIComponent(id) + (rest || ""));

//执行一个操作：出错时显示提示，无论成败最后都重画页面
//操作进行中（请求还没有返回）时不接受新的操作，页面上的按钮也暂时不可点击，
//避免连点两次造成重复提交
let busy = false;
async function act(operation) {
  if (busy) return;
  busy = true;
  document.body.classList.add("busy");
  try {
    await operation();
  } catch (error) {
    toast(error.message, true);
    //页面上的数据已经过期：放弃当前的预览，重新读取班级的最新数据
    if (error.stale && state.name) {
      try {
        setInfo((await api("GET", classPath())).class);
      } catch (reloadError) {
        toast(reloadError.message, true);
      }
    }
  }
  busy = false;
  document.body.classList.remove("busy");
  render();
}

// ---------- 3. 读取数据 ----------

//换上服务器返回的最新班级信息。原有的预览是按旧数据生成的，一并作废
function setInfo(info) {
  state.info = info;
  state.preview = null;
  state.picked = null;
  state.history = null;
  if (!state.editor || !state.editor.dirty) state.editor = editorFrom(info);
}

//切换到某个班级（name为null表示没有班级）
async function selectClass(name) {
  state.name = name;
  state.info = null;
  state.editor = null;
  state.draft = "";
  state.shown = null;
  state.historyLimit = 20;
  state.view.auto = true;       //换班级后座位图不同，回到自动缩放
  if (name) setInfo((await api("GET", classPath())).class);
  location.hash = name ? encodeURIComponent(name) + "/" + state.tab : "";
}

async function loadHistory() {
  state.history = await api("GET", classPath("/assignments?limit=" + state.historyLimit));
  const ids = state.history.assignments.map((a) => a.assignment_id);
  if (!ids.includes(state.shown)) state.shown = ids.length ? ids[0] : null;
}

// ---------- 4. 绘制 ----------

function render() {
  //顶栏
  const select = $("class-select");
  select.replaceChildren(...state.classes.map((name) => el("option", { value: name }, name)));
  if (state.name) select.value = state.name;
  select.disabled = !state.classes.length;
  $("class-rename").disabled = $("class-delete").disabled = !state.name;

  //页脚：版本号；桌面版多一个"打开数据文件夹"
  $("version").textContent = state.app.version ? "版本 " + state.app.version : "";
  $("open-data").hidden = !state.app.desktop;

  for (const button of $("tabs").children) {
    button.classList.toggle("active", button.dataset.tab === state.tab);
  }

  //页面内容
  let view;
  if (state.tab === "help") view = renderHelp();
  else if (!state.name) view = renderWelcome();
  else if (!state.info) view = el("div", { class: "empty" }, "读取中…");
  else if (state.tab === "arrange") view = renderArrange();
  else if (state.tab === "layout") view = renderLayout();
  else if (state.tab === "roster") view = renderRoster();
  else view = renderHistory();
  //班级还没有排过座时，页面上方显示新手引导条
  $("main").replaceChildren(...[guideStrip(), view].filter(Boolean));
  //座位图要等放进页面、有了实际尺寸之后才能计算缩放比例
  if ($("viewport")) {
    if (state.view.auto) fitView();
    else applyView();
  }
}

// --- 新手引导 ---

//还没有任何班级时显示的欢迎页：说明四个步骤，并提供"新建班级"和"示例班级"两个入口
function renderWelcome() {
  const step = (number, title, text) => el("li", {}, el("span", { class: "step-number" }, String(number)),
    el("div", {}, el("strong", {}, title), el("div", { class: "hint" }, text)));
  return el("div", { class: "card welcome" },
    el("h2", {}, "欢迎使用座位随机重排"),
    el("p", { class: "empty-note" }, "还没有班级。按下面四步就能排出第一张座位表："),
    el("ol", { class: "steps" },
      step(1, "新建班级", "给班级起个名字，比如“高一3班”。可以建立多个班级，互不影响。"),
      step(2, "画座位表", "在“座位表”页的格子上点一点，教室里哪儿有座位就点哪儿。"),
      step(3, "添加名单", "在“名单”页把学生姓名粘贴进去，一行一个。"),
      step(4, "排座", "在“排座”页点“生成新方案”，满意后点“确认并保存”。")),
    el("div", { class: "toolbar" },
      el("button", { class: "primary big", id: "welcome-new", onclick: newClass }, "新建班级"),
      el("button", { class: "big", id: "welcome-demo", onclick: createDemo }, "先用示例班级试一试"),
      el("button", { class: "link", onclick: () => { state.tab = "help"; render(); } }, "查看使用帮助")),
    el("p", { class: "hint" }, "示例班级里有现成的座位表和 32 名“同学”，可以随便试，试完在右上角“删除班级”即可。"));
}

//新手引导条：班级还没有保存过排座方案时显示在页面上方，指出现在该做哪一步。保存第一次排座后不再出现
function guideStrip() {
  const info = state.info;
  if (!state.name || !info || info.latest || state.tab === "help") return null;
  const seats = info.seats.length;
  const students = info.students.filter((s) => s.active).length;
  //每一步：[名称, 是否已完成, 完成情况的说明, 对应的页面]
  const steps = [
    ["画座位表", seats > 0, seats ? "已有 " + seats + " 个座位" : "还没有座位", "layout"],
    ["添加名单", students > 0, students ? "已有 " + students + " 名学生" : "还没有学生", "roster"],
    ["生成并保存第一次排座", false, seats && students && seats < students ? "座位比学生少，需要先加座位" : "", "arrange"],
  ];
  const current = steps.findIndex((step) => !step[1]);       //第一个没完成的步骤
  const [title, , , tab] = steps[current];
  return el("div", { class: "guide", id: "guide" },
    el("strong", {}, "新手引导"),
    steps.map(([name, done, note], index) => el("span", {
      class: "guide-step" + (done ? " done" : "") + (index === current ? " current" : ""),
    }, (done ? "✓ " : (index + 1) + ". ") + name, note && el("span", { class: "hint" }, "（" + note + "）"))),
    state.tab !== tab && el("button", { class: "small primary", id: "guide-go", onclick: () => {
      state.tab = tab;
      location.hash = encodeURIComponent(state.name) + "/" + tab;
      render();
    } }, "去" + title + " →"));
}

//建立一个示例班级：6排、每排3组双人座共36个座位，32名学生。供第一次使用的人直接体验
function createDemo() {
  act(async () => {
    let name = "示例班级";
    for (let number = 2; state.classes.includes(name); number++) name = "示例班级" + number;
    name = (await api("POST", "classes", { name })).name;
    const base = "classes/" + encodeURIComponent(name);
    const positions = [];
    for (let x = 1; x <= 6; x++) for (const y of [1, 2, 4, 5, 7, 8]) positions.push([x, y]);    //第3、6列空着当过道
    await api("PUT", base + "/seats", { positions });
    const students = [];
    for (let number = 1; number <= 32; number++) students.push("同学" + String(number).padStart(2, "0"));
    await api("POST", base + "/students", { student_ids: students });
    state.classes = (await api("GET", "classes")).classes;
    state.tab = "arrange";
    await selectClass(name);
    toast("示例班级已经建好。点“生成新方案”试试看");
  });
}

// --- 帮助页 ---

//帮助页的内容：[小标题, [段落, ...]]。写给不熟悉电脑的使用者，尽量用平常的话
const HELP_SECTIONS = [
  ["四步排出一张座位表", [
    "第 1 步：点右上方的“新建班级”，输入班级名称。",
    "第 2 步：点“座位表”，在格子上点击，教室里哪儿有座位就点哪儿（再点一次取消），点完后按“保存座位表”。黑板在最上方。",
    "第 3 步：点“名单”，在左边的框里输入学生姓名，一行一个（可以从 Excel 或 Word 里复制后粘贴），然后按“添加”。",
    "第 4 步：点“排座”，按“生成新方案”。这时只是预览，不满意可以按“重新生成”，也可以点两个座位把两名学生对调。满意后按“确认并保存”。",
    "以后每次换座位，只需要重复第 4 步。",
  ]],
  ["排座页的操作", [
    "对调学生：预览时先点一个座位，再点另一个座位，两人互换；点一个有人的座位再点一个空座，就是把人挪过去。",
    "放大、缩小和移动：滚动鼠标滚轮可以放大缩小，按住鼠标拖动可以移动座位图，按“适应窗口”恢复原样。",
    "排错了：按“撤销上一次排座”，回到上一次的座位。",
    "“显示座位负担”和“展开历史负担表”默认是关着的，投屏给学生看时不会显示这些数字；想看时再打开。",
  ]],
  ["名单的维护", [
    "新同学转来：在“名单”页添加即可。",
    "同学转走或长期请假：按他那一行的“标记离开”。他不再参与排座，但以前的记录都保留；回来时按“标记回来”。",
    "名字写错了：按“改名”。只有从来没有排过座的学生才能“删除”。",
    "从文件导入：把名单存成文本文件（.txt，一行一个名字），按“从文本文件读取”选择它。",
  ]],
  ["它是怎么保证公平的", [
    "每个座位有一个“负担值”：越靠近教室正中间越小（好座位），越靠边角越大（差座位）。",
    "每个学生坐过的所有座位的负担值加起来，就是他的“历史负担”。一直坐边角的学生，历史负担会比别人高。",
    "每次排座，程序会随机生成 100 个方案。生成时，历史负担高的学生优先挑选，并且更容易抽到好座位；同时尽量让每个人换到离黑板远近不同、离原位置较远的地方。",
    "然后给每个方案打分：排完之后全班的历史负担越接近，分数越高。最后选出分数最高的那个方案。这就是“公平性分数”，越接近 0 越公平。",
    "所以单看一次是随机的，长期看每个人坐好座位和差座位的机会是均衡的。",
  ]],
  ["数据和备份", [
    "所有数据（班级、名单、每次的座位）都自动保存在这台电脑上，不需要手动保存，也不会上传到网络。",
    "删除班级之前，程序会自动备份一次，保留最近 10 份。",
    "换电脑：把“数据文件夹”整个复制到新电脑上同样的位置即可。",
  ]],
];

function renderHelp() {
  const app = state.app;
  return el("div", { class: "card help" },
    el("h2", {}, "使用帮助"),
    app.desktop && el("div", { class: "toolbar" },
      el("button", { class: "primary", id: "open-help", onclick: () => openLocal("help") }, "打开完整的使用说明（带图）"),
      el("button", { onclick: () => openLocal("data") }, "打开数据文件夹")),
    HELP_SECTIONS.map(([title, paragraphs]) => el("section", {},
      el("h3", {}, title), paragraphs.map((text) => el("p", {}, text)))),
    app.data_dir && el("p", { class: "hint" }, "数据文件夹的位置：" + app.data_dir));
}

//桌面版：让程序打开数据文件夹或使用说明文件
function openLocal(what) {
  act(async () => { await api("POST", "app/open", { what }); });
}

//画一个教室：黑板在上，座位按坐标排成网格
//positions为座位坐标[[x, y], ...]，cellFor(x, y)返回该座位的页面元素
function roomView(positions, cellFor) {
  const xs = positions.map((p) => p[0]);
  const ys = positions.map((p) => p[1]);
  const minX = Math.min(...xs);
  const minY = Math.min(...ys);
  const grid = el("div", { class: "grid" });
  grid.style.gridTemplateRows = "repeat(" + (Math.max(...xs) - minX + 1) + ", var(--cell))";
  grid.style.gridTemplateColumns = "repeat(" + (Math.max(...ys) - minY + 1) + ", var(--cell))";
  for (const [x, y] of positions) {
    const cell = cellFor(x, y);
    cell.style.gridRow = x - minX + 1;        //x是前后方向：x越小越靠近黑板，画在越上面
    cell.style.gridColumn = y - minY + 1;     //y是左右方向
    grid.append(cell);
  }
  return el("div", { class: "room-scroll" },
    el("div", { class: "room" }, el("div", { class: "blackboard" }, "黑板"), grid));
}

//座位的底色表示座位负担：负担越大（离中心越远）颜色越深
function burdenColor(burden) {
  return "hsl(213 70% " + (97 - (burden + 1) / 2 * 19) + "%)";
}

function burdenLegend() {
  return el("div", { class: "legend" }, "座位负担：小（靠近中心）", el("span", { class: "ramp" }), "大（远离中心）");
}

// --- 排座页 ---

//座位图的缩放范围，以及自动缩放时最多放大到多少（座位很少时不至于放得过大）
const MIN_SCALE = 0.05;
const MAX_SCALE = 4;
const MAX_AUTO_SCALE = 1.5;
//对调动画的时长（毫秒）。系统设置了"减少动态效果"时不播放动画
const SWAP_MS = window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 450;

//按state.view摆放座位图，并更新缩放比例的显示
function applyView() {
  const view = state.view;
  $("stage").style.transform = "translate(" + view.x + "px, " + view.y + "px) scale(" + view.scale + ")";
  $("zoom-label").textContent = Math.round(view.scale * 100) + "%";
}

//自动缩放：让整个座位图刚好完整地显示在显示区域里，并居中
function fitView() {
  const viewport = $("viewport");
  const stage = $("stage");
  const margin = 16;      //四周留出的空白
  //offsetWidth、offsetHeight是座位图没有缩放时的原始尺寸
  let scale = Math.min((viewport.clientWidth - 2 * margin) / stage.offsetWidth,
    (viewport.clientHeight - 2 * margin) / stage.offsetHeight, MAX_AUTO_SCALE);
  scale = Math.max(scale, MIN_SCALE);
  state.view = {
    scale,
    x: (viewport.clientWidth - stage.offsetWidth * scale) / 2,
    y: (viewport.clientHeight - stage.offsetHeight * scale) / 2,
    auto: true,
  };
  applyView();
}

//以显示区域中的点(cx, cy)为中心缩放factor倍：缩放前后这个点对着座位图上的同一个位置
function zoomAt(factor, cx, cy) {
  const view = state.view;
  const scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, view.scale * factor));
  view.x = cx - (cx - view.x) * scale / view.scale;
  view.y = cy - (cy - view.y) * scale / view.scale;
  view.scale = scale;
  view.auto = false;
  applyView();
}

//给座位图的显示区域加上滚轮缩放和拖动
function enableZoomAndDrag(viewport) {
  viewport.addEventListener("wheel", (event) => {
    event.preventDefault();       //不让页面跟着滚动
    const box = viewport.getBoundingClientRect();
    zoomAt(event.deltaY < 0 ? 1.15 : 1 / 1.15, event.clientX - box.left, event.clientY - box.top);
  }, { passive: false });

  //拖动：按下后移动超过4像素才算拖动，否则仍是一次普通的点击（预览时点击座位用于对调）
  let drag = null;
  let dragEndTime = -1;       //上一次拖动结束（松开鼠标）的时刻
  viewport.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    drag = { startX: event.clientX, startY: event.clientY, x: state.view.x, y: state.view.y, moved: false };
  });
  viewport.addEventListener("pointermove", (event) => {
    if (!drag) return;
    const dx = event.clientX - drag.startX;
    const dy = event.clientY - drag.startY;
    if (!drag.moved && Math.hypot(dx, dy) > 4) {
      drag.moved = true;
      viewport.setPointerCapture(event.pointerId);     //鼠标移出显示区域后仍然继续拖动
      viewport.classList.add("dragging");
    }
    if (drag.moved) {
      state.view.x = drag.x + dx;
      state.view.y = drag.y + dy;
      state.view.auto = false;
      applyView();
    }
  });
  const endDrag = (event) => {
    if (drag && drag.moved) {
      viewport.classList.remove("dragging");
      dragEndTime = event.timeStamp;
    }
    drag = null;
  };
  viewport.addEventListener("pointerup", endDrag);
  viewport.addEventListener("pointercancel", endDrag);
  //拖动结束时松开鼠标会紧接着触发一次点击，这次点击不应当被当成点选座位。
  //它与松开鼠标是同一个动作，发生的时刻几乎相同；而人再次点击至少要隔几十毫秒，据此区分
  viewport.addEventListener("click", (event) => {
    if (Math.abs(event.timeStamp - dragEndTime) < 5) {
      event.stopPropagation();
      event.preventDefault();
    }
  }, true);
}

//对调动画：让学生的名字从原座位"飞"到新座位。moves为[{from, to, name}, ...]，from、to是座位"x,y"
//做法：在座位图上方临时放一块写着名字的牌子，从原座位移动到新座位；期间隐藏两个座位上原有的名字。
//动画结束后由调用方重画页面，牌子随之消失，座位上显示新的名字
function animateSwap(moves) {
  const stage = $("stage");
  if (!stage || !SWAP_MS) return Promise.resolve();
  const scale = state.view.scale;
  const origin = stage.getBoundingClientRect();
  //座位在座位图中的位置和大小（换算回没有缩放时的尺寸）
  const boxOf = (seatKey) => {
    const seat = stage.querySelector('[data-seat="' + seatKey + '"]');
    const rect = seat.getBoundingClientRect();
    seat.querySelector(".who").style.visibility = "hidden";
    seat.classList.remove("picked");
    return { x: (rect.left - origin.left) / scale, y: (rect.top - origin.top) / scale,
      width: rect.width / scale, height: rect.height / scale };
  };
  for (const move of moves) {
    const from = boxOf(move.from);
    const to = boxOf(move.to);
    const ghost = el("div", { class: "ghost" }, move.name);
    ghost.style.left = from.x + "px";
    ghost.style.top = from.y + "px";
    ghost.style.width = from.width + "px";
    ghost.style.height = from.height + "px";
    ghost.style.transitionDuration = SWAP_MS + "ms";
    stage.append(ghost);
    ghost.getBoundingClientRect();      //让浏览器先按起点位置排好，下一行的移动才会有过渡动画
    ghost.style.transform = "translate(" + (to.x - from.x) + "px, " + (to.y - from.y) + "px)";
  }
  return new Promise((resolve) => setTimeout(resolve, SWAP_MS));
}

function renderArrange() {
  const info = state.info;
  const active = info.students.filter((s) => s.active);
  if (!info.seats.length) {
    return el("div", { class: "empty" }, "还没有设置座位表。请先到“座位表”页点选座位。");
  }
  if (!active.length) {
    return el("div", { class: "empty" }, "名单里还没有在班的学生。请先到“名单”页添加学生。");
  }

  const preview = state.preview;
  //current：现在每个座位上的学生；shown：页面上要显示的（预览时是预览方案里的）
  const current = {};
  for (const seat of info.seats) current[key(seat.x, seat.y)] = seat.student;
  let shown = current;
  if (preview) {
    shown = {};
    for (const [id, item] of Object.entries(preview.assignments)) shown[key(...item.current_seat)] = id;
  }

  //上方：预览时是提示条和确认按钮，平时是"生成新方案"和"撤销"
  let top;
  if (preview) {
    top = el("div", { class: "banner" },
      el("div", {}, el("strong", {}, "预览中，尚未保存"),
        "点击两个座位可以对调学生，也可以把学生移到空座位。"),
      el("div", { class: "toolbar", style: "margin: 10px 0 0" },
        el("span", {}, "公平性分数 ", el("span", { class: "score", id: "preview-score" }, preview.score.toFixed(3)),
          el("span", { class: "hint" }, "（越接近 0 越公平）")),
        el("button", { class: "primary", id: "confirm", onclick: confirmPlan }, "确认并保存"),
        el("button", { id: "regenerate", onclick: generate }, "重新生成"),
        el("button", { id: "discard", onclick: discard }, "放弃")));
  } else {
    const latest = info.latest;
    top = el("div", { class: "toolbar" },
      el("button", { class: "primary", id: "generate", onclick: generate }, "生成新方案"),
      el("button", { id: "rollback", disabled: !latest, onclick: rollback }, "撤销上一次排座"),
      el("span", { class: "hint" }, latest
        ? "最近一次排座：编号 " + latest.assignment_id + "，" + latest.created_at.replace("T", " ") +
          "，公平性分数 " + latest.score.toFixed(3)
        : "还没有排过座"));
  }

  //座位图。roomView返回的外层是带滚动条的容器，这里只要里面的教室本身，由下面的显示区域负责缩放和移动
  const room = roomView(info.seats.map((s) => [s.x, s.y]), (x, y) => {
    const seat = info.seats.find((s) => s.x === x && s.y === y);
    const id = shown[key(x, y)];
    const classes = ["seat"];
    if (!id) classes.push("vacant");
    if (!state.showBurden) classes.push("plain");
    //预览中，坐的人与现在不同的座位加上橙色边框（两边都是空座不算不同）
    if (preview && (id || null) !== (current[key(x, y)] || null)) classes.push("moved");
    if (state.picked === key(x, y)) classes.push("picked");
    //预览时座位是可以点击的按钮，平时只是显示
    const cell = el(preview ? "button" : "div", {
      class: classes.join(" "),
      title: "座位 (" + x + ", " + y + ")" + (state.showBurden ? "，座位负担 " + signed(seat.burden) : ""),
      "data-seat": key(x, y),
      onclick: preview ? () => pickSeat(x, y) : null,
    }, el("span", { class: "who" }, id || "空座"),
      //座位负担的数字和底色只在开启"显示座位负担"后出现
      state.showBurden && el("span", { class: "sub" }, signed(seat.burden)));
    if (state.showBurden) cell.style.background = burdenColor(seat.burden);
    return cell;
  }).firstChild;

  const viewport = el("div", { class: "viewport", id: "viewport" }, el("div", { class: "stage", id: "stage" }, room));
  enableZoomAndDrag(viewport);
  //以显示区域的中心为准放大或缩小
  const zoomButton = (factor) => () => zoomAt(factor, viewport.clientWidth / 2, viewport.clientHeight / 2);

  const controls = el("div", { class: "toolbar view-controls" },
    el("button", { class: "small", id: "zoom-out", title: "缩小", onclick: zoomButton(1 / 1.25) }, "−"),
    el("span", { class: "zoom-label", id: "zoom-label" }, "100%"),
    el("button", { class: "small", id: "zoom-in", title: "放大", onclick: zoomButton(1.25) }, "+"),
    el("button", { class: "small", id: "zoom-fit", onclick: fitView }, "适应窗口"),
    el("span", { class: "hint" }, "滚轮缩放，按住拖动可移动"),
    el("span", { class: "spacer" }),
    el("label", { class: "check" },
      el("input", { type: "checkbox", id: "show-burden", checked: state.showBurden, onchange: (event) => {
        state.showBurden = event.target.checked;
        savePreference("showBurden", state.showBurden);
        render();
      } }), "显示座位负担"),
    el("button", { class: "small", id: "toggle-table", onclick: () => {
      state.showTable = !state.showTable;
      savePreference("showTable", state.showTable);
      render();
    } }, state.showTable ? "收起历史负担表 ▸" : "展开历史负担表 ◂"));

  const roomCard = el("div", { class: "card grow" }, controls, viewport, state.showBurden && burdenLegend());
  if (!state.showTable) return el("div", {}, top, roomCard);

  //右侧（展开时）：每个学生的历史负担；预览时多出"排座后"和"变化"两列
  const rows = active.map((s) => {
    const cells = [el("td", {}, s.student_id), el("td", { class: "num" }, signed(s.accum_burden))];
    if (preview) {
      const after = preview.assignments[s.student_id].burden;
      const change = after - s.accum_burden;
      cells.push(el("td", { class: "num" }, signed(after)),
        el("td", { class: "num " + (change > 0 ? "up" : "down") }, signed(change)));
    }
    return el("tr", {}, cells);
  });
  const head = [el("th", {}, "学生"), el("th", { class: "num" }, "历史负担")];
  if (preview) head.push(el("th", { class: "num" }, "排座后"), el("th", { class: "num" }, "变化"));

  return el("div", {}, top, el("div", { class: "columns" }, roomCard,
    el("div", { class: "card side", id: "burden-table" },
      el("h2", {}, "历史负担（" + active.length + " 人，" + info.seats.length + " 个座位）"),
      el("div", { class: "table-scroll" },
        el("table", {}, el("thead", {}, el("tr", {}, head)), el("tbody", {}, rows))))));
}

// --- 座位表页 ---

//按班级现有的座位表建立编辑器的内容。编辑器是一块rows行cols列的格子，
//左上角的格子对应坐标(x0, y0)，cells是已选为座位的格子
function editorFrom(info) {
  const xs = info.seats.map((s) => s.x);
  const ys = info.seats.map((s) => s.y);
  const x0 = Math.min(1, ...xs);
  const y0 = Math.min(1, ...ys);
  return {
    x0, y0,
    //比现有座位多留一行一列，方便向外扩展，但不超出坐标的最大值
    rows: Math.min(MAX_COORDINATE - x0 + 1, Math.max(8, Math.max(0, ...xs) - x0 + 2)),
    cols: Math.min(MAX_COORDINATE - y0 + 1, Math.max(10, Math.max(0, ...ys) - y0 + 2)),
    cells: new Set(info.seats.map((s) => key(s.x, s.y))),
    dirty: false,                                        //是否有尚未保存的改动
  };
}

function renderLayout() {
  const editor = state.editor;
  const active = state.info.students.filter((s) => s.active).length;

  //改变行列数；减少时去掉落在范围之外的座位
  //坐标最大为MAX_COORDINATE（与后端config.MAX_COORDINATE一致），格子不能加到这个范围之外
  const resize = (rowChange, colChange) => () => {
    editor.rows = Math.min(MAX_COORDINATE - editor.x0 + 1, Math.max(1, editor.rows + rowChange));
    editor.cols = Math.min(MAX_COORDINATE - editor.y0 + 1, Math.max(1, editor.cols + colChange));
    for (const cell of [...editor.cells]) {
      const [x, y] = cell.split(",").map(Number);
      if (x >= editor.x0 + editor.rows || y >= editor.y0 + editor.cols) {
        editor.cells.delete(cell);
        editor.dirty = true;
      }
    }
    render();
  };

  const positions = [];
  for (let r = 0; r < editor.rows; r++) {
    for (let c = 0; c < editor.cols; c++) positions.push([editor.x0 + r, editor.y0 + c]);
  }
  const room = roomView(positions, (x, y) => el("button", {
    class: "cell" + (editor.cells.has(key(x, y)) ? " on" : ""),
    title: "(" + x + ", " + y + ")",
    "data-cell": key(x, y),
    onclick: () => {
      if (!editor.cells.delete(key(x, y))) editor.cells.add(key(x, y));
      editor.dirty = true;
      render();
    },
  }));

  return el("div", { class: "card editor" },
    el("div", { class: "toolbar" },
      el("button", { class: "primary", id: "save-seats", disabled: !editor.dirty, onclick: saveSeats }, "保存座位表"),
      el("button", { disabled: !editor.dirty, onclick: () => { state.editor = editorFrom(state.info); render(); } }, "还原"),
      el("button", { onclick: resize(1, 0) }, "加一排"),
      el("button", { onclick: resize(-1, 0) }, "减一排"),
      el("button", { onclick: resize(0, 1) }, "加一列"),
      el("button", { onclick: resize(0, -1) }, "减一列"),
      el("button", { onclick: () => { editor.cells.clear(); editor.dirty = true; render(); } }, "清空"),
      el("span", { class: "hint", id: "seat-count" },
        "已选 " + editor.cells.size + " 个座位，在班学生 " + active + " 人" + (editor.dirty ? "（有未保存的改动）" : ""))),
    el("p", { class: "hint" }, "点击格子添加或取消座位。黑板的位置会根据座位自动确定在最前方正中。"),
    room);
}

// --- 名单页 ---
function renderRoster() {
  const students = state.info.students;
  const active = students.filter((s) => s.active).length;

  const rows = students.map((s) => el("tr", { class: s.active ? "" : "inactive", "data-student": s.student_id },
    el("td", {}, s.student_id),
    el("td", {}, s.active ? "在班" : "已离开"),
    el("td", { class: "num" }, signed(s.accum_burden)),
    el("td", { class: "num" }, String(s.rounds)),
    el("td", {}, s.current_seat ? "(" + s.current_seat.join(", ") + ")" : "—"),
    el("td", {}, el("div", { class: "actions" },
      el("button", { class: "small", disabled: !s.rounds, onclick: () => showStudentHistory(s.student_id) }, "历史"),
      el("button", { class: "small", onclick: () => renameStudent(s.student_id) }, "改名"),
      el("button", { class: "small", onclick: () => setActive(s.student_id, !s.active) }, s.active ? "标记离开" : "标记回来"),
      //有排座历史的学生不能删除（删掉后历史方案会缺人），只能标记离开
      el("button", { class: "small danger", disabled: s.rounds > 0,
        title: s.rounds > 0 ? "已有排座历史的学生不能删除，请标记离开" : "", onclick: () => deleteStudent(s.student_id) }, "删除")))));

  const input = el("textarea", { id: "names", rows: "8", placeholder: "每行一个学生的学号或姓名，也可以用逗号分隔",
    oninput: (event) => { state.draft = event.target.value; } });
  input.value = state.draft;

  return el("div", { class: "columns" },
    el("div", { class: "card side" }, el("h2", {}, "添加学生"), input,
      el("div", { class: "toolbar", style: "margin: 10px 0 0" },
        el("button", { class: "primary", id: "add-students", onclick: addStudents }, "添加"),
        el("label", { class: "hint" }, "从文本文件读取 ",
          el("input", { type: "file", accept: ".txt,.csv,text/plain", onchange: readNamesFile })))),
    el("div", { class: "card grow" },
      el("h2", {}, "名单（在班 " + active + " 人，已离开 " + (students.length - active) + " 人）"),
      el("p", { class: "hint" }, "中途加入或离开后回来的学生，历史负担从全班在班学生当时的平均值开始。"),
      students.length
        ? el("table", {}, el("thead", {}, el("tr", {}, el("th", {}, "学生"), el("th", {}, "状态"),
            el("th", { class: "num" }, "历史负担"), el("th", { class: "num" }, "排座次数"), el("th", {}, "当前座位"),
            el("th", {}, "操作"))), el("tbody", {}, rows))
        : el("div", { class: "empty" }, "名单还是空的")));
}

// --- 历史页 ---
function renderHistory() {
  if (!state.history) {
    //还没有读取：先显示"读取中"，读到之后重画
    //读取失败时记下failed，改为显示重试按钮；否则重画时会不停地自动重试
    act(async () => {
      try {
        await loadHistory();
      } catch (error) {
        state.history = { assignments: [], has_more: false, failed: true };
        throw error;
      }
    });
    return el("div", { class: "empty" }, "读取中…");
  }
  if (state.history.failed) {
    return el("div", { class: "empty" }, "历史记录读取失败。 ",
      el("button", { onclick: () => { state.history = null; render(); } }, "重试"));
  }
  const list = state.history.assignments;
  if (!list.length) return el("div", { class: "empty" }, "还没有排座记录");

  const rows = list.map((a) => el("tr", {
    class: "clickable" + (a.assignment_id === state.shown ? " selected" : ""),
    "data-assignment": a.assignment_id,
    onclick: () => { state.shown = a.assignment_id; render(); },
  },
    el("td", { class: "num" }, String(a.assignment_id)),
    el("td", {}, a.created_at.replace("T", " ")),
    el("td", { class: "num" }, a.score.toFixed(3)),
    el("td", { class: "num" }, String(Object.keys(a.assignments).length))));

  const side = el("div", { class: "card side" }, el("h2", {}, "排座记录（新的在前）"),
    el("table", {}, el("thead", {}, el("tr", {}, el("th", { class: "num" }, "编号"), el("th", {}, "时间"),
      el("th", { class: "num" }, "分数"), el("th", { class: "num" }, "人数"))), el("tbody", {}, rows)),
    state.history.has_more && el("div", { class: "toolbar", style: "margin: 12px 0 0" },
      el("button", { onclick: () => act(async () => { state.historyLimit += 20; await loadHistory(); }) }, "加载更早的记录")));

  //右侧：选中方案的座位图。座位取"这个方案用到的座位"和"现在的座位表"的并集，
  //方案里有、现在座位表里已经没有的座位用虚线框表示
  const plan = list.find((a) => a.assignment_id === state.shown);
  const who = {};
  for (const [id, item] of Object.entries(plan.assignments)) who[key(...item.current_seat)] = [id, item.burden];
  const burdenAt = {};
  for (const seat of state.info.seats) burdenAt[key(seat.x, seat.y)] = seat.burden;
  const all = new Set([...Object.keys(who), ...Object.keys(burdenAt)]);
  const room = roomView([...all].map((k) => k.split(",").map(Number)), (x, y) => {
    const entry = who[key(x, y)];
    const exists = key(x, y) in burdenAt;
    const cell = el("div", { class: "seat" + (entry ? "" : " vacant") + (exists ? "" : " gone"),
      title: exists ? "" : "这个座位已不在现在的座位表中" },
      el("span", { class: "who" }, entry ? entry[0] : "空座"),
      el("span", { class: "sub" }, entry ? "累计 " + signed(entry[1]) : ""));
    if (exists) cell.style.background = burdenColor(burdenAt[key(x, y)]);
    return cell;
  });

  return el("div", { class: "columns" }, side,
    el("div", { class: "card grow" },
      el("h2", {}, "方案 " + plan.assignment_id + "　" + plan.created_at.replace("T", " ") +
        "　公平性分数 " + plan.score.toFixed(3)),
      room, el("p", { class: "hint" }, "座位上的数字是该次排座后学生的累计历史负担。")));
}

// ---------- 5. 操作 ----------

// --- 班级 ---
async function newClass() {
  const name = await ask("新班级的名称（例如：高一3班）：", { input: true });
  if (name === null) return;
  act(async () => {
    const result = await api("POST", "classes", { name });
    state.classes = result.classes;
    await selectClass(result.name);       //服务器返回的是整理后的名称（去掉了多余的空格）
  });
}

async function renameClass() {
  const name = await ask("班级的新名称：", { input: true, value: state.name });
  if (name === null || name.trim() === state.name) return;
  act(async () => {
    const result = await api("PUT", classPath(), { name });
    state.classes = result.classes;
    state.name = result.class.name;
    setInfo(result.class);
    location.hash = encodeURIComponent(state.name) + "/" + state.tab;
    toast("已改名为 " + state.name);
  });
}

//删除班级无法恢复，要求输入班级名称确认，避免误点
async function deleteClass() {
  const name = state.name;
  const typed = await ask("删除班级会同时删除它的座位表、名单和全部排座历史。\n" +
    "（删除前程序会自动备份一次数据。）\n\n请输入班级名称“" + name + "”确认删除：",
    { input: true, okText: "删除", danger: true });
  if (typed === null) return;
  if (typed.trim() !== name) {
    toast("输入的名称不一致，没有删除", true);
    return;
  }
  act(async () => {
    state.classes = (await api("DELETE", classPath())).classes;
    await selectClass(state.classes[0] || null);
    toast("已删除班级 " + name);
  });
}

// --- 排座 ---
function generate() {
  act(async () => {
    state.preview = await api("POST", classPath("/preview"));
    state.picked = null;
  });
}

function discard() {
  state.preview = null;
  state.picked = null;
  render();
}

//预览方案里每个学生的座位{student_id: [x, y]}，即发回服务器的形式
function previewPlan() {
  const plan = {};
  for (const [id, item] of Object.entries(state.preview.assignments)) plan[id] = item.current_seat;
  return plan;
}

//预览中点击座位：第一次点击选中，再点另一个座位就对调两个座位上的学生，然后请服务器重新打分
//对调时不直接改掉座位上的名字，而是播放名字移动到新座位的动画；动画播完、服务器也返回了新的分数之后，
//再把页面换成新方案
function pickSeat(x, y) {
  const here = key(x, y);
  if (state.picked === null) { state.picked = here; render(); return; }
  if (state.picked === here) { state.picked = null; render(); return; }

  const plan = previewPlan();
  const there = state.picked;
  const occupant = (seatKey) => Object.keys(plan).find((id) => key(...plan[id]) === seatKey);
  const a = occupant(there);
  const b = occupant(here);
  if (!a && !b) { state.picked = here; render(); return; }     //两个都是空座，改为选中新点的这个
  const moves = [];
  if (a) { plan[a] = [x, y]; moves.push({ from: there, to: here, name: a }); }
  if (b) { plan[b] = there.split(",").map(Number); moves.push({ from: here, to: there, name: b }); }

  //base是生成预览时班级最新方案的编号，原样发回；服务器发现数据已经变化时会拒绝
  act(async () => {
    state.picked = null;
    //动画和请求同时进行，两者都完成后再换上新方案。请求失败时act会显示提示并重画，页面回到对调前的样子
    const [result] = await Promise.all([
      api("POST", classPath("/score"), { plan, base: state.preview.base }),
      animateSwap(moves),
    ]);
    state.preview = result;
  });
}

function confirmPlan() {
  act(async () => {
    const result = await api("POST", classPath("/assignments"), { plan: previewPlan(), base: state.preview.base });
    setInfo(result.class);
    toast("已保存，方案编号 " + result.assignment_id);
  });
}

async function rollback() {
  const latest = state.info.latest;
  if (!await ask("撤销最近一次排座（编号 " + latest.assignment_id + "）？\n这条记录会被删除，无法恢复。",
    { okText: "撤销", danger: true })) return;
  act(async () => {
    //带上要撤销的方案编号：如果它已经不是最新的方案（例如已经被撤销过），服务器会拒绝
    const result = await api("DELETE", classPath("/assignments/latest"), { assignment_id: latest.assignment_id });
    setInfo(result.class);
    toast("已撤销方案 " + result.assignment_id);
  });
}

// --- 座位表 ---
async function saveSeats() {
  const cells = state.editor.cells;
  const positions = [...cells].map((cell) => cell.split(",").map(Number))
    .sort((p, q) => p[0] - q[0] || p[1] - q[1]);
  if (positions.length > MAX_SEATS) {
    toast("座位太多了（每个班最多" + MAX_SEATS + "个）", true);
    return;
  }

  //提醒：现在坐着人的座位如果被去掉，这些学生会暂时没有座位
  const lost = state.info.students.filter((s) => s.current_seat && !cells.has(key(...s.current_seat))).length;
  if (lost && !await ask("有 " + lost + " 名学生现在的座位将被去掉，他们会暂时没有座位（历史负担保留）。\n继续保存吗？",
    { okText: "继续保存" })) return;

  act(async () => {
    const result = await api("PUT", classPath("/seats"), { positions });
    state.editor.dirty = false;
    setInfo(result.class);
    toast("座位表已保存，共 " + positions.length + " 个座位");
  });
}

// --- 名单 ---
function addStudents() {
  const names = state.draft.split(/[\n\r,，、;；\t]+/).map((name) => name.trim()).filter(Boolean);
  if (!names.length) { toast("请先输入学生的学号或姓名", true); return; }
  act(async () => {
    setInfo((await api("POST", classPath("/students"), { student_ids: names })).class);
    state.draft = "";
    toast("已添加 " + names.length + " 名学生");
  });
}

//把文件内容（字节）转成文字。先按UTF-8读；读不通时按GBK读——
//Windows记事本等软件保存的中文文本常常是GBK编码，按UTF-8读会变成乱码
function decodeText(buffer) {
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(buffer);
  } catch (error) {
    return new TextDecoder("gbk").decode(buffer);
  }
}

//把选择的文本文件的内容放进输入框，由用户检查后再点"添加"
function readNamesFile(event) {
  const file = event.target.files[0];
  if (!file) return;
  if (file.size > 200 * 1024) {
    toast("文件太大了（超过200KB），请确认选择的是名单的文本文件", true);
    event.target.value = "";
    return;
  }
  const reader = new FileReader();
  reader.onload = () => {
    const text = decodeText(reader.result);
    //文本文件里不应该有这些控制字符，出现了说明选的不是文本文件（例如Excel表格）
    if (/[\u0000-\u0008\u000e-\u001f]/.test(text)) {
      toast("这个文件不是文本文件，无法读取。Excel表格请先另存为文本，或直接复制粘贴名单", true);
    } else {
      state.draft = (state.draft ? state.draft.trimEnd() + "\n" : "") + text;
    }
    render();
  };
  reader.onerror = () => toast("文件读取失败", true);
  reader.readAsArrayBuffer(file);
}

async function renameStudent(id) {
  const name = await ask("新的学号或姓名：", { input: true, value: id });
  if (name === null || name.trim() === id) return;
  act(async () => setInfo((await api("PUT", studentPath(id), { student_id: name })).class));
}

function setActive(id, active) {
  act(async () => setInfo((await api("PUT", studentPath(id), { active })).class));
}

async function deleteStudent(id) {
  if (!await ask("从名单中删除 " + id + "？", { okText: "删除", danger: true })) return;
  act(async () => setInfo((await api("DELETE", studentPath(id))).class));
}

function showStudentHistory(id) {
  act(async () => {
    const track = (await api("GET", studentPath(id, "/history"))).history;
    const dialog = $("dialog");
    dialog.replaceChildren(
      el("h2", {}, id + " 的座位历史"),
      el("table", {}, el("thead", {}, el("tr", {}, el("th", { class: "num" }, "方案编号"), el("th", {}, "时间"),
        el("th", {}, "座位"), el("th", { class: "num" }, "累计历史负担"))),
        el("tbody", {}, track.map((t) => el("tr", {},
          el("td", { class: "num" }, String(t.assignment_id)),
          el("td", {}, t.created_at.replace("T", " ")),
          el("td", {}, "(" + t.seat.join(", ") + ")"),
          el("td", { class: "num" }, signed(t.burden)))))),
      el("div", { class: "toolbar" }, el("button", { onclick: () => dialog.close() }, "关闭")));
    dialog.showModal();
  });
}

// --- 其他 ---
function checkIntegrity() {
  act(async () => {
    const problems = (await api("GET", "integrity")).problems;
    if (problems.length) toast("发现 " + problems.length + " 个问题：" + problems.join("；"), true);
    else toast("数据完整，没有发现问题");
  });
}

// ---------- 启动 ----------
window.addEventListener("resize", () => {
  if ($("viewport") && state.view.auto) fitView();
});
$("class-select").addEventListener("change", (event) => act(() => selectClass(event.target.value)));
$("class-new").addEventListener("click", newClass);
$("class-rename").addEventListener("click", renameClass);
$("class-delete").addEventListener("click", deleteClass);
$("check").addEventListener("click", checkIntegrity);
$("open-data").addEventListener("click", () => openLocal("data"));
for (const button of $("tabs").children) {
  button.addEventListener("click", () => {
    state.tab = button.dataset.tab;
    if (state.name) location.hash = encodeURIComponent(state.name) + "/" + state.tab;
    render();
  });
}

//打开网页时：读取班级列表。网址"#班级/页面"里记着上次所在的班级和页面，有的话回到那里
act(async () => {
  state.app = await api("GET", "app");
  state.classes = (await api("GET", "classes")).classes;
  const [hashName, hashTab] = location.hash.slice(1).split("/");
  let wanted = null;
  try {
    wanted = decodeURIComponent(hashName || "");
  } catch (error) {
    wanted = null;      //网址被手动改坏时，当作没有指定班级
  }
  if (["arrange", "layout", "roster", "history", "help"].includes(hashTab)) state.tab = hashTab;
  await selectClass(state.classes.includes(wanted) ? wanted : state.classes[0] || null);
});
