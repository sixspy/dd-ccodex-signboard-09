window.SB_VIEWS = window.SB_VIEWS || {};
window.SB_VIEWS["history-view"] = {
  template: `
  <div class="panel">
    <h2>变更历史</h2>
    <p class="lead">发布、撤屏、覆盖与台账变更同源可追溯。按单号检索为跨表联查，只命中已落正式区的发布。</p>
    <div class="row-form">
      <input v-model="changeNo" placeholder="变更单号 CHG-YYYY-序号" />
      <button class="primary" @click="search">按单号检索</button>
      <button class="ghost" @click="reset">查看全部</button>
    </div>
    <p class="hint" v-if="searched">检索口径：仅正式区发布可命中；未升正影子草案不参与，避免当成已发布齐套。</p>
    <table>
      <thead><tr><th>ID</th><th>实体</th><th>动作</th><th>变更单号</th><th>详情</th><th>操作者</th></tr></thead>
      <tbody>
        <tr v-for="h in items" :key="h.id">
          <td>{{ h.id }}</td><td>{{ h.entity_kind }}#{{ h.entity_id }}</td>
          <td>{{ h.action }}</td><td>{{ h.change_no || '—' }}</td>
          <td>{{ h.detail }}</td><td>{{ h.actor }}</td>
        </tr>
      </tbody>
    </table>
    <p v-if="searched && items.length === 0" class="err">该单号在正式区查无已发布记录（影子未升正不会命中）。</p>
  </div>`,
  data() { return { items: [], changeNo: "", searched: false }; },
  async mounted() { await this.loadAll(); },
  methods: {
    async loadAll() {
      this.searched = false;
      this.items = (await SB.api("/api/history")).items;
    },
    async search() {
      const key = (this.changeNo || "").trim();
      if (!key) { await this.loadAll(); return; }
      this.searched = true;
      this.items = (await SB.api("/api/history?change_no=" + encodeURIComponent(key))).items;
    },
    async reset() {
      this.changeNo = "";
      await this.loadAll();
    }
  }
};
