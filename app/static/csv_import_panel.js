// CSV 导入面板的两个小工具，节点页（工作区草稿）与插入节点页共用。
// 曾各自抄了一份，改一处漏一处，抽到这里。

// 表单级 hx-trigger="change" 连 mode 单选按钮的切换也算数：没选文件就切换
// 「差异 / 全量」会发一次注定失败的预览请求，回一句「请选择 CSV 文件」——
// 用户本来就正打算去选，这提醒纯属打断。守卫在触发条件里：没文件不发请求。
// 选文件那一次 change 触发时 files 已就位，正常路径不受影响。
// 必须返回严格布尔 true：htmx 的判定是 `filter.call(elt, evt) !== true` 即
// 取消事件，返回 files.length 这类 truthy 非 true 的值会把所有请求都静默屏蔽。
function importHasFile(){
  const input = document.querySelector('#import-form input[type=file]');
  return !!(input && input.files && input.files.length);
}

// 「取消全部」：丢弃这份预览。必须连文件输入框的值一起清掉——否则再选同一个
// 文件不会触发 change 事件，导入就此卡死。mode 单选归 Alpine 管，不动它。
function cancelImport(){
  const input = document.querySelector('#import-form input[type=file]');
  if (input) input.value = '';
  const box = document.getElementById('import-preview');
  if (box) box.innerHTML = '';
}

// 预览请求失败的就地兜底：返回的是 HTML 片段，失败时 htmx 什么都不换，
// 指示条一消失、预览区空着，用户只会以为没反应。两页共用同一段文案。
function importRequestFailed(evt){
  const box = document.getElementById('import-preview');
  if (!box) return;
  const status = evt && evt.detail && evt.detail.xhr ? evt.detail.xhr.status : 0;
  box.innerHTML = '<div class="flash flash-error">✕ 导入检查失败'
    + (status ? '（服务端返回 ' + status + '）' : '：连不上服务')
    + '，请重试</div>';
}
