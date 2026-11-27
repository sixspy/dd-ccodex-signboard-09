window.SB_VIEWS = window.SB_VIEWS || {};
window.SB_VIEWS["publish-view"] = {
  template: `
  <div class="two-col">
    <div>
      <div class="panel">
        <h2>发布上屏</h2>
        <p class="lead">甲：正式事务直写（缺合规单号整笔拒写）；乙：影子草案补号后升正。两边不得混用。</p>
        <p class="hint">闸口 signboard-09：发布摘要须匹配变更单号正则 CHG-YYYY-序号（如 CHG-2026-0007），并双写可检索字段。</p>
        <div class="row-form">
          <input v-model.number="form.screen_id" type="number" placeholder="屏幕ID" />
          <input v-model.number="form.playlist_id" type="number" placeholder="清单ID" />
          <input v-model.number="form.window_id" type="number" placeholder="时段ID" />
          <input v-model="form.summary" placeholder="摘要须含 CHG-YYYY-序号" />
        </div>
        <div class="row-form">
          <button class="primary" @click="official">正式发布（甲）</button>
          <button class="ghost" @click="shadow">写影子草案（乙）</button>
          <input v-model.number="shadowId" type="number" placeholder="影子ID" />
          <input v-model="patchSummary" placeholder="补号摘要 CHG-YYYY-序号" />
          <button class="ghost" @click="patchShadow">补号</button>
          <button class="primary" @click="promote">升正影子</button>
        </div>
        <p class="err" v-if="error">{{ error }}</p>
        <p v-if="msg">{{ msg }}</p>
      </div>
      <div class="panel">
        <h3>正式发布</h3>
        <table>
          <thead><tr><th>ID</th><th>屏幕</th><th>清单</th><th>时段</th><th>版本</th><th>变更单号</th><th>摘要</th></tr></thead>
          <tbody>
            <tr v-for="p in official" :key="p.id">
              <td>{{ p.id }}</td><td>{{ p.screen_code }}</td><td>{{ p.playlist_code }}</td>
              <td>{{ p.window_code }}</td><td>v{{ p.version }}</td>
              <td>{{ p.change_no || '—' }}</td><td>{{ p.summary }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
    <div class="panel">
      <h3>影子草案</h3>
      <table>
        <thead><tr><th>ID</th><th>屏幕</th><th>状态</th><th>变更单号</th><th>摘要</th></tr></thead>
        <tbody>
          <tr v-for="s in shadows" :key="s.id">
            <td>{{ s.id }}</td><td>{{ s.screen_code }}</td><td>{{ s.status }}</td>
            <td>{{ s.change_no || '待补号' }}</td><td>{{ s.summary }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>`,
  data() {
    return {
      form: { screen_id: 1, playlist_id: 1, window_id: 1, summary: "电梯厅单号摘要案 CHG-2026-0001 发布" },
      shadowId: null,
      patchSummary: "补号草案 CHG-2026-0002",
      official: [], shadows: [], error: "", msg: ""
    };
  },
  async mounted() { await this.reload(); },
  methods: {
    async reload() {
      this.official = (await SB.api("/api/publish/official")).items;
      this.shadows = (await SB.api("/api/publish/shadows")).items;
    },
    async official() {
      this.error = ""; this.msg = "";
      try {
        const r = await SB.api("/api/publish/official", { method: "POST", body: JSON.stringify(this.form) });
        this.msg = "正式发布完成 version=" + r.version + " 单号=" + r.change_no; await this.reload();
      } catch (e) { this.error = e.message; }
    },
    async shadow() {
      this.error = ""; this.msg = "";
      try {
        const r = await SB.api("/api/publish/shadow", { method: "POST", body: JSON.stringify(this.form) });
        this.msg = "影子已写 id=" + r.id + (r.change_no ? " 单号=" + r.change_no : "（待补号，正式区零改）");
        this.shadowId = r.id; await this.reload();
      } catch (e) { this.error = e.message; }
    },
    async patchShadow() {
      this.error = ""; this.msg = "";
      if (!this.shadowId) { this.error = "请先填写影子ID"; return; }
      try {
        const r = await SB.api("/api/publish/shadow/" + this.shadowId, {
          method: "PATCH", body: JSON.stringify({ summary: this.patchSummary })
        });
        this.msg = "补号完成 id=" + r.id + " 单号=" + (r.change_no || "未识别");
        await this.reload();
      } catch (e) { this.error = e.message; }
    },
    async promote() {
      this.error = ""; this.msg = "";
      try {
        const r = await SB.api("/api/publish/shadow/" + this.shadowId + "/promote", { method: "POST", body: "{}" });
        this.msg = "升正完成 version=" + r.version + " 单号=" + r.change_no; await this.reload();
      } catch (e) { this.error = e.message; }
    }
  }
};
