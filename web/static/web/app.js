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
  tab: "arrange",     //当前页面：arrange排座、layout座位表、roster名单、history历史
  preview: null,      //正在预览的方案{score, assignments}；没有预览时为null
  picked: null,       //预览中已点选、等待对调的座位"x,y"
  editor: null,       //座位表编辑器的内容{x0, y0, rows, cols, cells, dirty}
  draft: "",          //名单页输入框里还没有提交的文字
  history: null,      //历史页的数据{assignments, has_more}；还没有读取时为null
  historyLimit: 20,   //历史页读取的方案数量
  shown: null,        //历史页正在查看的方案编号
};

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

  for (const button of $("tabs").children) {
    button.classList.toggle("active", button.dataset.tab === state.tab);
  }

  //页面内容
  let view;
  if (!state.name) view = el("div", { class: "empty" }, "还没有班级。点击上方的“新建班级”开始。");
  else if (!state.info) view = el("div", { class: "empty" }, "读取中…");
  else if (state.tab === "arrange") view = renderArrange();
  else if (state.tab === "layout") view = renderLayout();
  else if (state.tab === "roster") view = renderRoster();
  else view = renderHistory();
  $("main").replaceChildren(view);
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

  const room = roomView(info.seats.map((s) => [s.x, s.y]), (x, y) => {
    const seat = info.seats.find((s) => s.x === x && s.y === y);
    const id = shown[key(x, y)];
    const classes = ["seat"];
    if (!id) classes.push("vacant");
    //预览中，坐的人与现在不同的座位加上橙色边框（两边都是空座不算不同）
    if (preview && (id || null) !== (current[key(x, y)] || null)) classes.push("moved");
    if (state.picked === key(x, y)) classes.push("picked");
    //预览时座位是可以点击的按钮，平时只是显示
    const cell = el(preview ? "button" : "div", {
      class: classes.join(" "),
      title: "座位 (" + x + ", " + y + ")，座位负担 " + signed(seat.burden),
      "data-seat": key(x, y),
      onclick: preview ? () => pickSeat(x, y) : null,
    }, el("span", { class: "who" }, id || "空座"), el("span", { class: "sub" }, signed(seat.burden)));
    cell.style.background = burdenColor(seat.burden);
    return cell;
  });

  //右侧：每个学生的历史负担；预览时多出"排座后"和"变化"两列
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

  return el("div", {}, top, el("div", { class: "columns" },
    el("div", { class: "card grow" }, room, burdenLegend()),
    el("div", { class: "card side" }, el("h2", {}, "历史负担（" + active.length + " 人，" + info.seats.length + " 个座位）"),
      el("table", {}, el("thead", {}, el("tr", {}, head)), el("tbody", {}, rows)))));
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
function newClass() {
  const name = prompt("新班级的名称：");
  if (name === null) return;
  act(async () => {
    const result = await api("POST", "classes", { name });
    state.classes = result.classes;
    await selectClass(result.name);       //服务器返回的是整理后的名称（去掉了多余的空格）
  });
}

function renameClass() {
  const name = prompt("班级的新名称：", state.name);
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
function deleteClass() {
  const name = state.name;
  const typed = prompt("删除班级会同时删除它的座位表、名单和全部排座历史，而且无法恢复。\n" +
    "请输入班级名称“" + name + "”确认删除：");
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
  if (a) plan[a] = [x, y];
  if (b) plan[b] = there.split(",").map(Number);

  //base是生成预览时班级最新方案的编号，原样发回；服务器发现数据已经变化时会拒绝
  act(async () => {
    state.picked = null;
    state.preview = await api("POST", classPath("/score"), { plan, base: state.preview.base });
  });
}

function confirmPlan() {
  act(async () => {
    const result = await api("POST", classPath("/assignments"), { plan: previewPlan(), base: state.preview.base });
    setInfo(result.class);
    toast("已保存，方案编号 " + result.assignment_id);
  });
}

function rollback() {
  const latest = state.info.latest;
  if (!confirm("撤销最近一次排座（编号 " + latest.assignment_id + "）？\n这条记录会被删除，无法恢复。")) return;
  act(async () => {
    //带上要撤销的方案编号：如果它已经不是最新的方案（例如已经被撤销过），服务器会拒绝
    const result = await api("DELETE", classPath("/assignments/latest"), { assignment_id: latest.assignment_id });
    setInfo(result.class);
    toast("已撤销方案 " + result.assignment_id);
  });
}

// --- 座位表 ---
function saveSeats() {
  const cells = state.editor.cells;
  const positions = [...cells].map((cell) => cell.split(",").map(Number))
    .sort((p, q) => p[0] - q[0] || p[1] - q[1]);
  if (positions.length > MAX_SEATS) {
    toast("座位太多了（每个班最多" + MAX_SEATS + "个）", true);
    return;
  }

  //提醒：现在坐着人的座位如果被去掉，这些学生会暂时没有座位
  const lost = state.info.students.filter((s) => s.current_seat && !cells.has(key(...s.current_seat))).length;
  if (lost && !confirm("有 " + lost + " 名学生现在的座位将被去掉，他们会暂时没有座位（历史负担保留）。\n继续保存吗？")) return;

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

function renameStudent(id) {
  const name = prompt("新的学号或姓名：", id);
  if (name === null || name.trim() === id) return;
  act(async () => setInfo((await api("PUT", studentPath(id), { student_id: name })).class));
}

function setActive(id, active) {
  act(async () => setInfo((await api("PUT", studentPath(id), { active })).class));
}

function deleteStudent(id) {
  if (!confirm("从名单中删除 " + id + "？")) return;
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
$("class-select").addEventListener("change", (event) => act(() => selectClass(event.target.value)));
$("class-new").addEventListener("click", newClass);
$("class-rename").addEventListener("click", renameClass);
$("class-delete").addEventListener("click", deleteClass);
$("check").addEventListener("click", checkIntegrity);
for (const button of $("tabs").children) {
  button.addEventListener("click", () => {
    state.tab = button.dataset.tab;
    if (state.name) location.hash = encodeURIComponent(state.name) + "/" + state.tab;
    render();
  });
}

//打开网页时：读取班级列表。网址"#班级/页面"里记着上次所在的班级和页面，有的话回到那里
act(async () => {
  state.classes = (await api("GET", "classes")).classes;
  const [hashName, hashTab] = location.hash.slice(1).split("/");
  let wanted = null;
  try {
    wanted = decodeURIComponent(hashName || "");
  } catch (error) {
    wanted = null;      //网址被手动改坏时，当作没有指定班级
  }
  if (["arrange", "layout", "roster", "history"].includes(hashTab)) state.tab = hashTab;
  await selectClass(state.classes.includes(wanted) ? wanted : state.classes[0] || null);
});
