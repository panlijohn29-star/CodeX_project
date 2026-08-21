(function (global) {
  function escapeHtml(value) { return String(value).replace(/[&<>]/g, function (c) { return {"&":"&amp;", "<":"&lt;", ">":"&gt;"}[c]; }); }
  function highlight(sql) {
    var source = String(sql || ""), out = "", index = 0;
    var token = /(--[^\n]*|\/\*[\s\S]*?\*\/|'(?:''|[^'])*'|"(?:""|[^"])*"|`[^`]*`|\b\d+(?:\.\d+)?\b|\b(?:SELECT|FROM|WHERE|INSERT|INTO|UPDATE|DELETE|CREATE|ALTER|DROP|TABLE|JOIN|LEFT|RIGHT|INNER|OUTER|ON|AS|AND|OR|NOT|NULL|IS|IN|LIKE|BETWEEN|ORDER|BY|GROUP|HAVING|LIMIT|OFFSET|UNION|ALL|DISTINCT|SET|VALUES|BEGIN|COMMIT|ROLLBACK|SHOW|DESCRIBE|EXPLAIN|CASE|WHEN|THEN|ELSE|END)\b)/gi;
    var match;
    while ((match = token.exec(source))) {
      out += escapeHtml(source.slice(index, match.index));
      var text = escapeHtml(match[0]), cls = "cm-keyword";
      if (/^(--|\/\*)/.test(match[0])) cls = "cm-comment";
      else if (/^['"`]/.test(match[0])) cls = "cm-string";
      else if (/^\d/.test(match[0])) cls = "cm-number";
      out += '<span class="' + cls + '">' + text + '</span>';
      index = token.lastIndex;
    }
    return out + escapeHtml(source.slice(index)) + "\n";
  }
  global.CodeMirror = function (host, options) {
    var wrapper = document.createElement("div"), code = document.createElement("pre"), textarea = document.createElement("textarea");
    wrapper.className = "CodeMirror"; textarea.spellcheck = false; textarea.wrap = "off"; textarea.value = (options && options.value) || "";
    wrapper.appendChild(code); wrapper.appendChild(textarea); host.replaceWith(wrapper);
    var changeHandlers = [];
    function refresh() { code.innerHTML = highlight(textarea.value); }
    function changed() { refresh(); changeHandlers.forEach(function (handler) { handler(textarea.value); }); }
    textarea.addEventListener("input", changed); textarea.addEventListener("scroll", function () { code.scrollTop = textarea.scrollTop; code.scrollLeft = textarea.scrollLeft; });
    textarea.addEventListener("keydown", function (event) { if (event.key === "Tab") { event.preventDefault(); var start = textarea.selectionStart, end = textarea.selectionEnd; textarea.setRangeText("  ", start, end, "end"); changed(); } });
    refresh();
    return { getValue: function () { return textarea.value; }, setValue: function (value) { textarea.value = value || ""; changed(); }, onChange: function (handler) { changeHandlers.push(handler); }, focus: function () { textarea.focus(); } };
  };
}(window));
