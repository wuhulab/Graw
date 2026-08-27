<!--
  综合设置中心窗口
  业务：聚合面板级设置——用户管理、VIP 状态、ShunX 安全入口、多节点（SSH/Agent）管理、Web 引擎模式、两步验证、界面偏好、语言、版本更新。
  后端模块：/api/ui、/api/vip、/api/nodes、/api/agent、/api/webmode、/api/auth（2FA）、/api/update、/api/shunx、/api/health
  关键状态：节点/Web模式/2FA/更新/版本等多区块响应式数据；dangerConfirm（删除节点/清除入口高危二次确认）
  打开方式：桌面「设置」入口挂载
-->
<template>
  <div style="display:flex; flex-direction:column; height:100%; background:#f5f5f7;">
    <div style="flex:1; overflow:auto; padding:16px; display:flex; flex-direction:column; gap:14px;">
      <div class="block">
        <div class="block-title">{{ $t('settings.title') }}</div>
        <button class="btn" @click="emit('openUsers')" :disabled="!isAdmin()">{{ $t('settings.openUsers') }}</button>
        <span v-if="!isAdmin()" style="font-size:11px;color:#6e6e73;margin-left:8px;">{{ $t('common.adminOnly') }}</span>
      </div>

      <!-- 付费功能：当前月卡/年卡状态 + 续费月卡（授权地址在后端固定，前端不可改） -->
      <div class="block">
        <div class="block-title">{{ $t('vip.title') }}</div>
        <div class="row" style="justify-content:space-between; padding:2px 0;">
          <span :class="['tag', isVip() ? 'tag-current' : '']" style="font-size:12px;">{{ vipStatusText }}</span>
          <button class="btn btn-mini" @click="emit('openVip')">{{ $t('vip.renew') }}</button>
        </div>
        <div style="font-size:11px;color:#8e8e93;line-height:1.6;">{{ $t('vip.renewHint') }}</div>
      </div>

      <!-- ShunX 安全入口管理（仅管理员） -->
      <div class="block" v-if="isAdmin()">
        <div class="block-title">{{ $t('settings.shunxTitle') }}</div>
        <div class="row" style="flex-wrap:wrap; gap:6px;">
          <span class="status-dot" :class="currentEntry ? 'on' : 'off'"></span>
          <span style="font-size:12px;color:#1d1d1f;">
            {{ statusText }}
          </span>
        </div>
        <div class="row" style="flex-direction:column; align-items:stretch; gap:8px;">
          <input v-model="entryPath" :placeholder="$t('settings.shunxPlaceholder')" spellcheck="false" @keyup.enter="saveEntry" />
          <div style="display:flex; gap:8px;">
            <button class="btn" :disabled="saving" @click="saveEntry">{{ saving ? $t('settings.saveSaving') : $t('settings.save') }}</button>
            <button class="btn btn-danger" v-if="currentEntry" :disabled="saving" @click="clearEntry">{{ $t('settings.clearEntry') }}</button>
          </div>
          <div v-if="msg" :class="['msg', msgType]">{{ msg }}</div>
        </div>
        <div style="font-size:11px;color:#8e8e93;line-height:1.6;margin-top:4px;">
          {{ $t('settings.entryHint', { url: origin + '/' + (currentEntry || '...') }) }}
        </div>
      </div>

      <!-- 多机（多节点）管理（仅管理员） -->
      <div class="block" v-if="isAdmin()">
        <div class="block-title">{{ $t('nodes.title') }}</div>
        <div style="font-size:11px;color:#8e8e93;line-height:1.6;margin-bottom:8px;">{{ $t('nodes.subtitle') }}</div>

        <!-- 列表加载 / 重新加载 -->
        <div class="row" style="gap:8px;">
          <button class="btn btn-mini" :disabled="loadingNodes" @click="loadNodes">
            {{ loadingNodes ? $t('nodes.loadingNodes') : $t('nodes.reload') }}
          </button>
          <span v-if="loadingNodes" style="font-size:11px;color:#8e8e93;">{{ $t('nodes.loadingNodes') }}…</span>
        </div>

        <!-- 当前管理主机 -->
        <div class="row" style="gap:8px;">
          <span class="status-dot" style="background:#0a7d3b;"></span>
          <span style="font-size:12px;color:#1d1d1f;">{{ $t('nodes.currentLabel') }}</span>
          <span style="font-size:12px;font-weight:600;color:#1d1d1f;">{{ current.name || currentId }}</span>
          <span :class="['tag', current.type === 'ssh' ? 'tag-remote' : 'tag-local']">{{ current.type === 'ssh' ? $t('nodes.remoteBadge') : $t('nodes.localBadge') }}</span>
        </div>

        <!-- 统一面板兼容开关（付费功能）：开启后每个窗口绑定打开时对应的节点，聚焦窗口即操作该节点。
             非付费用户该选项锁定，需「付费解锁」开通 VIP 后方可开启。 -->
        <div class="row" style="justify-content:space-between; padding:2px 0;">
          <label class="switch-label" :style="!isVip() ? { opacity: 0.5, cursor: 'not-allowed' } : {}">
            <input type="checkbox" v-model="settings.unifiedPanel" :disabled="!isVip()" />
            <span style="font-size:12px;font-weight:600;color:#1d1d1f;">
              {{ $t('nodes.unifiedPanel') }} <span v-if="!isVip()" style="color:#c0392b;">· {{ $t('vip.paid') }}</span>
            </span>
          </label>
          <span v-if="isVip()" class="tag tag-current">{{ $t('vip.active') }}</span>
          <button v-else class="btn btn-mini" @click="emit('openVip')">{{ $t('vip.unlock') }}</button>
        </div>
        <div style="font-size:11px;color:#8e8e93;line-height:1.6;margin-bottom:6px;">
          {{ isVip() ? $t('nodes.unifiedPanelHint') : $t('vip.lockedHint') }}
        </div>

        <!-- 测试连接独立反馈区 -->
        <div v-if="connMsg" :class="['msg', connMsgType]" style="margin-top:6px;">{{ connMsg }}</div>

        <!-- 节点列表 -->
        <div v-if="nodesList.length" style="display:flex;flex-direction:column;gap:6px;margin-top:4px;">
          <div v-for="n in nodesList" :key="n.id" class="node-item">
            <label class="switch-label" style="flex:1;min-width:0;">
              <input type="radio" name="currentHost" :value="n.id" :checked="n.id === currentId" @change="switchNode(n)" />
              <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">
                {{ n.name }}
                <span v-if="n.type === 'ssh'" style="color:#8e8e93;font-size:11px;">{{ n.user }}@{{ n.host }}:{{ n.port }}</span>
              </span>
            </label>
            <span v-if="n.id === currentId" class="tag tag-current">{{ $t('nodes.current') }}</span>
            <span v-if="n.agent_enabled" class="tag tag-agenty">{{ $t('nodes.agentBadge') }}</span>
            <button class="btn btn-mini" @click="testNode(n)">{{ testingId === n.id ? $t('nodes.testing') : $t('nodes.test') }}</button>
            <button class="btn btn-mini" v-if="n.type === 'ssh'" @click="startEdit(n)">{{ $t('nodes.edit') }}</button>
            <button class="btn btn-mini btn-danger" v-if="n.type === 'ssh'" @click="removeNode(n)">{{ $t('nodes.delete') }}</button>
          </div>
        </div>
        <div v-else-if="!loadingNodes" style="font-size:12px;color:#8e8e93;padding:6px 0;">{{ $t('nodes.noNodes') }}</div>
        <div v-if="nodeLoadError" :class="['msg', 'err']" style="margin-top:6px;">{{ nodeLoadError }}</div>

        <div v-if="!showEditor" style="margin-top:8px;">
          <button class="btn" @click="startAdd">{{ $t('nodes.addNode') }}</button>
        </div>

        <!-- 添加 / 编辑 SSH 节点表单 -->
        <div v-if="showEditor" class="editor">
          <div class="row" style="flex-direction:column;align-items:stretch;gap:6px;">
            <input v-model="form.name" :placeholder="$t('nodes.namePlaceholder')" spellcheck="false" />
            <div class="row" style="gap:6px;">
              <input v-model="form.host" :placeholder="$t('nodes.hostPlaceholder')" spellcheck="false" style="flex:1;" />
              <input v-model.number="form.port" type="number" placeholder="22" style="width:70px;" />
            </div>
            <input v-model="form.user" :placeholder="$t('nodes.userPlaceholder')" spellcheck="false" />
            <div class="row" style="gap:12px;">
              <label class="switch-label"><input type="radio" name="auth" value="password" v-model="form.auth" /><span>{{ $t('nodes.authPassword') }}</span></label>
              <label class="switch-label"><input type="radio" name="auth" value="key" v-model="form.auth" /><span>{{ $t('nodes.authKey') }}</span></label>
            </div>
            <input v-if="form.auth === 'password'" v-model="form.password" type="password" :placeholder="$t('nodes.passwordPlaceholder')" spellcheck="false" />
            <input v-else v-model="form.key_path" :placeholder="$t('nodes.keyPathPlaceholder')" spellcheck="false" />
            <div style="font-size:11px;color:#8e8e93;">{{ form.auth === 'password' ? $t('nodes.passwordHint') : $t('nodes.keyHint') }}</div>

            <!-- Agent 配置（子节点 API）：让主面板能经 SSH 隧道回调子节点 Graw -->
            <div style="border-top:1px dashed rgba(0,0,0,0.12); margin:6px 0 8px; padding-top:10px;">
              <div class="row" style="justify-content:space-between; padding:0 0 2px;">
                <span style="font-size:12px;font-weight:600;color:#1d1d1f;">{{ $t('nodes.agentTitle') }}</span>
                <label class="switch-label">
                  <input type="checkbox" v-model="form.agent_enabled" />
                  <span style="font-size:11px;color:#8e8e93;">{{ $t('nodes.agentEnable') }}</span>
                </label>
              </div>
              <div style="font-size:11px;color:#8e8e93;line-height:1.6;margin-bottom:6px;">{{ $t('nodes.agentHint') }}</div>
              <template v-if="form.agent_enabled">
                <input v-model.number="form.agent_port" type="number" :placeholder="$t('nodes.agentPortPlaceholder')" spellcheck="false" />
                <input v-model="form.agent_key" :placeholder="$t('nodes.agentKeyPlaceholder')" spellcheck="false" style="margin-top:6px;" />
                <input v-model="form.agent_secret" :placeholder="$t('nodes.agentSecretPlaceholder')" spellcheck="false" style="margin-top:6px;" />
                <div style="font-size:11px;color:#8e8e93;line-height:1.6;margin-top:4px;">{{ $t('nodes.agentSecretHint') }}</div>
              </template>
            </div>

            <div style="display:flex;gap:8px;">
              <button class="btn" :disabled="saving" @click="saveNode">{{ saving ? $t('settings.saveSaving') : $t('nodes.save') }}</button>
              <button class="btn btn-mini" @click="cancelEdit">{{ $t('nodes.cancel') }}</button>
            </div>
            <div v-if="editorMsg" :class="['msg', msgType]">{{ editorMsg }}</div>
          </div>
        </div>
      </div>

      <!-- 「作为子节点」Agent 收取模式（仅管理员）：让本面板自身可被其它主面板接入 -->
      <div class="block" v-if="isAdmin()">
        <div class="block-title">{{ $t('agent.title') }}</div>
        <div style="font-size:11px;color:#8e8e93;line-height:1.6;margin-bottom:8px;">{{ $t('agent.desc') }}</div>

        <!-- 当前状态开关 -->
        <div class="row" style="justify-content:space-between; padding:4px 0 6px;">
          <label class="switch-label">
            <input type="checkbox" v-model="agentForm.enabled" />
            <span style="font-size:12px;font-weight:600;color:#1d1d1f;">{{ $t('agent.enable') }}</span>
          </label>
          <span :class="['tag', agentStatus.enabled ? 'tag-remote' : 'tag-local']">{{ agentStatus.enabled ? $t('agent.enabled') : $t('agent.disabled') }}</span>
        </div>

        <template v-if="agentForm.enabled">
          <!-- 成对密钥配置 -->
          <div style="font-size:11px;color:#8e8e93;line-height:1.6;margin-bottom:8px;">
            {{ $t('agent.endpointHint') }}
          </div>
          <div class="row" style="flex-direction:column;align-items:stretch;gap:6px;">
            <input v-model="agentForm.key" :placeholder="$t('agent.keyLabel') + ' · ' + $t('agent.keyPlaceholder')" spellcheck="false" />
            <div class="row" style="gap:6px; padding:0;">
              <input v-model="agentForm.secret"
                     :type="agentSecretVisible ? 'text' : 'password'"
                     :placeholder="$t('agent.secretLabel') + ' · ' + $t('agent.secretPlaceholder')"
                     spellcheck="false" style="flex:1;" />
              <button class="btn btn-mini" type="button" :disabled="agentSaving || agentSecretBusy"
                      :title="$t('agent.toggleSecret')" @click="toggleAgentSecret">
                <svg v-if="!agentSecretVisible" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path>
                  <circle cx="12" cy="12" r="3"></circle>
                </svg>
                <svg v-else width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94"></path>
                  <path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19"></path>
                  <path d="M14.12 14.12a3 3 0 1 1-4.24-4.24"></path>
                  <line x1="1" y1="1" x2="23" y2="23"></line>
                </svg>
              </button>
              <button class="btn btn-mini" type="button" :disabled="agentSaving || agentSecretBusy"
                      :title="$t('agent.copySecret')" @click="copyAgentSecret">
                {{ agentSecretVisible ? $t('common.copy') : $t('agent.copySecret') }}
              </button>
            </div>
            <div style="font-size:11px;color:#8e8e93;">{{ $t('agent.secretHint') }}</div>
            <div v-if="agentSecretMsg" :class="['msg', agentSecretMsgType]" style="font-size:11px;">{{ agentSecretMsg }}</div>
          </div>
        </template>

        <div class="row" style="gap:8px; margin-top:8px;">
          <button class="btn" :disabled="agentSaving" @click="saveAgentCfg">
            {{ agentSaving ? $t('settings.saveSaving') : $t('settings.save') }}
          </button>
          <button class="btn btn-mini" @click="genAgentKey" :disabled="agentSaving">{{ $t('agent.genKey') }}</button>
          <div v-if="agentMsg" :class="['msg', agentMsgType]" style="flex:1;">{{ agentMsg }}</div>
        </div>
      </div>

      <!-- Web 服务器引擎模式（NGINX / OpenResty）（仅管理员） -->
      <div class="block" v-if="isAdmin()">
        <div class="block-title">{{ $t('settings.webmode.title') }}</div>
        <div style="font-size:11px;color:#8e8e93;line-height:1.6;margin-bottom:8px;">{{ $t('settings.webmode.desc') }}</div>
        <div class="row" style="flex-wrap:wrap; gap:16px;">
          <label class="switch-label">
            <input type="radio" name="webMode" value="nginx" v-model="wmMode" />
            <span>{{ $t('settings.webmode.nginx') }}</span>
          </label>
          <label class="switch-label">
            <input type="radio" name="webMode" value="openresty" v-model="wmMode" />
            <span>{{ $t('settings.webmode.openresty') }}</span>
          </label>
        </div>
        <!-- 可用性提示：两个引擎 + 当前配置目录 -->
        <div class="row" style="flex-direction:column; align-items:stretch; gap:4px;">
          <div style="font-size:11px;color:#8e8e93;">
            {{ $t('settings.webmode.bin', { bin: wmStatus.binary }) }}
            <span :class="['status-dot', wmStatus.available ? 'on' : 'off']"></span>
            {{ wmStatus.available ? $t('settings.webmode.installed') : $t('settings.webmode.notInstalled') }}
          </div>
          <div style="font-size:11px;color:#8e8e93;">
            {{ $t('settings.webmode.nginxBin') }}: <span :class="['status-dot', wmStatus.nginx_available ? 'on' : 'off']"></span>
            <span style="color:#1d1d1f;">{{ wmStatus.nginx_available ? $t('settings.webmode.installed') : $t('settings.webmode.notInstalled') }}</span>
            &nbsp;·&nbsp;
            {{ $t('settings.webmode.openrestyBin') }}: <span :class="['status-dot', wmStatus.openresty_available ? 'on' : 'off']"></span>
            <span style="color:#1d1d1f;">{{ wmStatus.openresty_available ? $t('settings.webmode.installed') : $t('settings.webmode.notInstalled') }}</span>
          </div>
          <div style="font-size:11px;color:#8e8e93;">
            {{ $t('settings.webmode.confDir', { dir: wmStatus.conf_base }) }}
          </div>
        </div>
        <div class="row" style="gap:8px;">
          <button class="btn" :disabled="wmsaving" @click="saveWebMode">
            {{ wmsaving ? $t('settings.saveSaving') : $t('settings.save') }}
          </button>
          <div v-if="wmmsg" :class="['msg', wmmsgType]" style="flex:1;">{{ wmmsg }}</div>
        </div>
      </div>

      <!-- 两步验证（2FA）：为当前账号开启 / 关闭 TOTP 动态口令 -->
      <div class="block">
        <div class="block-title">两步验证（2FA）</div>
        <div style="font-size:11px;color:#8e8e93;line-height:1.6;margin-bottom:8px;">
          开启后登录需输入密码 + 手机验证器（Google Authenticator 等）中的 6 位动态验证码
        </div>

        <template v-if="!otpState.enabled">
          <div class="row" style="align-items:center;">
            <span style="font-size:12.5px;">{{ otpState.has_secret ? '已生成密钥，扫描或手动输入后启用' : '尚未开启两步验证' }}</span>
            <button class="btn" :disabled="otpBusy" @click="otpSetup">{{ otpState.has_secret ? '重新生成密钥' : '开启两步验证' }}</button>
          </div>

          <!-- 密钥 / 二维码展示（setup 后） -->
          <div v-if="otpSecret" style="margin-top:10px;border:1px dashed #c7d2e0;border-radius:8px;padding:12px;background:#f6f8fb;">
            <img
              v-if="otpUri"
              :src="qrUrl(otpUri)"
              alt="2FA QR"
              style="width:120px;height:120px;border-radius:6px;margin-bottom:8px;"
              @error="otpUriQrFail = true"
            />
            <div v-if="otpUriQrFail" class="hint" style="color:#92400e;">无法加载二维码（离线），请手动添加：</div>
            <div class="mono" style="font-size:12px;word-break:break-all;">密钥：<code>{{ otpSecret }}</code></div>
            <div class="mono" style="font-size:11px;color:#6e6e73;word-break:break-all;margin-top:4px;">{{ otpUri }}</div>
            <div class="row" style="margin-top:10px;">
              <input v-model.trim="otpCode" placeholder="输入 6 位验证码" maxlength="6" inputmode="numeric" style="width:160px;" />
              <button class="btn btn-primary" :disabled="otpBusy || otpCode.length !== 6" @click="otpEnable">启用</button>
            </div>
          </div>
          <div v-if="otpMsg" :class="['msg', otpMsgType]" style="margin-top:8px;">{{ otpMsg }}</div>
        </template>

        <template v-else>
          <div class="row" style="align-items:center;">
            <span class="badge ok" style="padding:3px 10px;border-radius:999px;font-size:12px;font-weight:600;">已启用</span>
            <span style="font-size:12.5px;color:#6e6e73;">登录时需要动态验证码</span>
          </div>
          <div class="row" style="margin-top:8px;">
            <input v-model.trim="otpCode" placeholder="输入当前验证码以关闭" maxlength="6" inputmode="numeric" style="width:200px;" />
            <button class="btn btn-danger" :disabled="otpBusy || otpCode.length !== 6" @click="otpDisable">关闭两步验证</button>
          </div>
          <div v-if="otpMsg" :class="['msg', otpMsgType]" style="margin-top:8px;">{{ otpMsg }}</div>
        </template>
      </div>

      <div class="block">
        <div class="block-title">{{ $t('settings.panelTitle') }}</div>
        <div class="row">
          <label class="switch-label">
            <input type="checkbox" v-model="settings.showTaskbarText" />
            <span>{{ $t('settings.showTaskbarText') }}</span>
          </label>
        </div>
        <div class="row">
          <label class="switch-label">
            <input type="checkbox" v-model="settings.taskbarTextOnly" />
            <span>{{ $t('settings.taskbarTextOnly') }}</span>
          </label>
        </div>
        <div class="row">
          <label class="switch-label">
            <input type="checkbox" v-model="settings.hideFoxcode" />
            <span>{{ $t('settings.hideFoxcode') }}</span>
          </label>
        </div>
      </div>

      <!-- 回收站设置（仅管理员）：删除的文件是否进入回收站、到期自动清理天数 -->
      <div class="block" v-if="isAdmin()">
        <div class="block-title">{{ $t('recycle.settingsTitle') }}</div>
        <div class="row" style="justify-content:space-between; padding:4px 0;">
          <label class="switch-label">
            <input type="checkbox" v-model="rcForm.enabled" />
            <span style="font-size:12px;font-weight:600;color:#1d1d1f;">{{ $t('recycle.enabled') }}</span>
          </label>
        </div>
        <div style="font-size:11px;color:#8e8e93;line-height:1.6;">{{ $t('recycle.enabledHint') }}</div>
        <div class="row" style="justify-content:space-between; padding:4px 0;">
          <label class="switch-label" :style="rcForm.enabled ? {} : { opacity: 0.5 }">
            <input type="checkbox" v-model="rcForm.autoDelete" :disabled="!rcForm.enabled" />
            <span style="font-size:12px;font-weight:600;color:#1d1d1f;">{{ $t('recycle.autoDelete') }}</span>
          </label>
        </div>
        <div style="font-size:11px;color:#8e8e93;line-height:1.6;">{{ $t('recycle.autoDeleteHint') }}</div>
        <div class="row" style="gap:8px; padding:6px 0 10px;">
          <span style="font-size:12px;color:#1d1d1f;">{{ $t('recycle.days') }}</span>
          <input v-model.number="rcForm.days" type="number" min="1" max="365" style="width:90px;" :disabled="!rcForm.enabled || !rcForm.autoDelete" />
          <button class="btn" :disabled="rcSaving" @click="saveRecycle">
            {{ rcSaving ? $t('settings.saveSaving') : $t('recycle.save') }}
          </button>
          <span v-if="rcMsg" :class="['msg', rcMsgType]">{{ rcMsg }}</span>
        </div>
      </div>

      <!-- 界面语言 -->
      <div class="block">
        <div class="block-title">{{ $t('settings.language') }}</div>
        <div class="row" style="flex-wrap:wrap; gap:6px;">
          <label class="switch-label" v-for="lang in LANGUAGES" :key="lang.code" :style="{ fontWeight: settings.locale === lang.code ? 700 : 400 }">
            <input type="radio" name="locale" :value="lang.code" :checked="settings.locale === lang.code" @change="changeLocale(lang.code)" />
            <span>{{ lang.name }}</span>
          </label>
        </div>
      </div>

      <!-- 关于：项目与社区相关链接（外链新窗口打开，rel=noopener 防钓鱼） -->
      <div class="block">
        <div class="block-title">{{ $t('settings.about.title') }}</div>
        <div class="about-version" v-if="panelVersion">
          {{ $t('settings.about.version') }}: <span class="about-version-val">Graw v{{ panelVersion }}</span>
          <!-- 版本更新提示：发现新版显示一键更新；已最新则提示已是最新 -->
          <template v-if="updateAvailable">
            <span class="update-hint">→ {{ $t('settings.about.updateAvailable', { version: latestVersion }) }}</span>
            <button class="btn btn-update" :disabled="updating" @click="doUpdate">
              {{ updating ? $t('settings.about.updating') : $t('settings.about.updateNow') }}
            </button>
          </template>
          <span v-else-if="updateChecked" class="update-latest">{{ $t('settings.about.upToDate') }}</span>
        </div>
        <div v-if="updateMsg" :class="['msg', updateMsgType]" style="margin-bottom:8px;">{{ updateMsg }}</div>
        <div class="about-list">
          <a
            v-for="l in aboutLinks"
            :key="l.key"
            :href="l.url"
            target="_blank"
            rel="noopener noreferrer"
            class="about-link"
            :title="l.url"
          >
            <span class="about-name">{{ $t(l.nameKey) }}</span>
            <span class="about-url">{{ l.url }}</span>
          </a>
        </div>
      </div>
    </div>
  </div>

  <!-- 高风险操作二次确认：删除远程节点 / 清除安全入口等需输入面板密码 -->
  <ConfirmDialog
    :show="dangerConfirm.show"
    :mode="dangerConfirm.mode"
    :title="dangerConfirm.title"
    :message="dangerConfirm.message"
    :required-text="dangerConfirm.requiredText"
    :input-label="dangerConfirm.inputLabel"
    :placeholder="dangerConfirm.placeholder"
    :confirm-label="dangerConfirm.confirmLabel"
    @confirm="doConfirm"
    @cancel="dangerConfirm.show = false"
  />
</template>

<script setup>
import { ref, reactive, computed, onMounted, watch } from 'vue'   // 响应式、计算属性、挂载、监听 VIP 变化
import { useI18n } from 'vue-i18n'                               // 国际化：取 t() 生成动态文案
import { settings } from '../../store/settings'                 // 全局界面设置（任务栏/语言等）
import { isAdmin } from '../../store/auth'                      // 管理员门控：限定敏感区块
import { vip as vipStore, refreshVip, isVip } from '../../store/vip'   // VIP 状态：解锁付费功能
import { nodesApi, shunxApi, panelApi, updateApi, webmodeApi, authApi, agentApi, recycleApi } from '../../api'   // 各设置区块后端接口
import { nodes as nodesStore, refreshNodes, setCurrentNode } from '../../store/nodes'   // 多节点状态与当前节点切换
import { LANGUAGES, setLocale } from '../../locales'            // 语言清单与切换函数
import ConfirmDialog from '../ConfirmDialog.vue'                // 高危操作二次确认弹窗（输入面板密码）

const { t } = useI18n()
// 声明父组件（App.vue 窗口插槽）统一绑定的监听事件。本组件模板为双根
// （内容区 + ConfirmDialog），无法自动继承属性；全部声明为组件自定义事件后，
// Vue 不再尝试把它们落到 DOM 上，从而消除「Extraneous non-emits listener」告警。
// 其中仅 openUsers / openVip 会被本组件实际触发，其余为窗口系统公共事件。
const emit = defineEmits([
  'close', 'dirty',
  'openUsers', 'openVip',
  'openTerminal', 'openEditor', 'openMedia', 'openLogs',
  'openContainerTerminal', 'openContainerDetails', 'openContainerStats', 'openContainerEdit',
  'openFiles', 'openDockerConfigEditor',
  'openAppInstall', 'openComposeEditor', 'openInstallLog', 'openReadme',
  'openTaskCenter', 'openRuntimeCreate', 'openConnectionForm',
  'openNetStorageBrowse', 'openNetStorageForm', 'openSiteEdit',
])

// 付费功能：计算当前 VIP 剩余天数，用于「月卡生效中：45天」样式的状态展示
const remainingVipDays = computed(() => {
  if (!vipStore.vip_until) return 0
  const end = new Date(vipStore.vip_until).getTime()
  const diff = end - Date.now()
  return diff > 0 ? Math.max(1, Math.ceil(diff / 86400000)) : 0
})
const vipStatusText = computed(() => {
  if (!vipStore.vip) return t('vip.inactive')
  const plan = vipStore.plan === 'year' ? t('vip.year') : t('vip.month')
  return remainingVipDays.value > 0
    ? t('vip.activeDays', { plan, days: remainingVipDays.value })
    : t('vip.inactive')
})

// 付费门控：VIP 失效/未解锁时强制关闭「统一面板兼容」，保持默认关闭 + 锁定，
// 避免历史开启值在无授权状态下继续生效。
watch(() => vipStore.vip, (active) => {
  if (!active) settings.unifiedPanel = false
})

// 高风险操作二次确认状态（删除远程节点 / 清除安全入口等需输入面板密码）
// 注意：不能命名为 confirm，否则会遮蔽全局 window.confirm（doUpdate 仍在用）
const dangerConfirm = ref({ show: false, mode: 'password', title: '', message: '', requiredText: '', inputLabel: '', placeholder: '', confirmLabel: '', action: null })

// ShunX 安全入口状态
const entryPath = ref('')
const currentEntry = ref('')
const saving = ref(false)
const msg = ref('')
const msgType = ref('')
const origin = computed(() => window.location.origin)

// ---- Web 服务器引擎模式（NGINX / OpenResty）----
const wmMode = ref('nginx')
const wmStatus = reactive({ binary: 'nginx', available: false, nginx_available: false, openresty_available: false, conf_base: '/etc/nginx' })
const wmsaving = ref(false)
const wmmsg = ref('')
const wmmsgType = ref('')

async function loadWebMode() {
  try {
    const s = await webmodeApi.status()
    wmMode.value = s.mode || 'nginx'
    Object.assign(wmStatus, {
      binary: s.binary || 'nginx',
      available: !!s.available,
      nginx_available: !!s.nginx_available,
      openresty_available: !!s.openresty_available,
      conf_base: s.conf_base || '/etc/nginx',
    })
    wmmsg.value = ''
  } catch (e) {
    // 非管理员/接口异常时静默，不阻塞设置窗口其它功能
  }
}

async function saveWebMode() {
  if (wmsaving.value) return
  wmsaving.value = true
  wmmsg.value = ''
  try {
    const s = await webmodeApi.setMode(wmMode.value)
    wmMode.value = s.mode || wmMode.value
    Object.assign(wmStatus, {
      binary: s.binary || 'nginx',
      available: !!s.available,
      nginx_available: !!s.nginx_available,
      openresty_available: !!s.openresty_available,
      conf_base: s.conf_base || '/etc/nginx',
    })
    wmmsg.value = t('settings.webmode.saved', { bin: wmStatus.binary, dir: wmStatus.conf_base })
    wmmsgType.value = 'ok'
  } catch (e) {
    wmmsg.value = e?.response?.data?.detail || t('settings.webmode.saveFailed')
    wmmsgType.value = 'err'
  } finally {
    wmsaving.value = false
  }
}

// ---- 「作为子节点」Agent 收取模式 ----
const agentForm = reactive({ enabled: false, key: '', secret: '' })
const agentStatus = reactive({ enabled: false })
const agentSaving = ref(false)
const agentMsg = ref('')
const agentMsgType = ref('')
// secret 明文展示（一次性）：仅在「初次/重置后」允许拉取明文复制，展示后即隐藏
const agentSecretVisible = ref(false)
const agentSecretMsg = ref('')
const agentSecretMsgType = ref('')
// 拉取 secret / 复制过程中的忙态：防止并发点击
const agentSecretBusy = ref(false)

// 复制文本到剪贴板。优先 Clipboard API（仅 https/localhost 的 secure context 可用）；
// 面板常以纯 http 提供服务（如 http://ip:8041），此时 navigator.clipboard 不可用，
// 旧实现会直接报「复制失败」，故降级到隐藏 textarea + document.execCommand('copy')。
async function copyTextToClipboard(text) {
  if (text == null) return false
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch (e) {
      /* 权限被拒等：降级到 execCommand，不中断流程 */
    }
  }
  return fallbackCopy(text)
}

function fallbackCopy(text) {
  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.setAttribute('readonly', '')
    ta.style.position = 'fixed'
    ta.style.top = '-1000px'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    ta.setSelectionRange(0, text.length)
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    return ok
  } catch (e) {
    return false
  }
}

// 一次性拉取子节点校验 secret 明文（供复制/眼睛查看）。后端返回即标记已展示，第二次为空。
async function fetchAgentSecret() {
  if (agentSecretBusy.value) return ''
  agentSecretBusy.value = true
  try {
    const r = await agentApi.revealSecret()
    if (r.secret) {
      agentForm.secret = r.secret
      agentSecretVisible.value = true
      agentSecretMsg.value = t('agent.secretShownOnce')
      agentSecretMsgType.value = 'ok'
      return r.secret
    }
    agentSecretVisible.value = false
    agentSecretMsg.value = t('agent.secretAlreadyShown')
    agentSecretMsgType.value = 'err'
    return ''
  } catch (e) {
    const d = e?.response?.data?.detail
    agentSecretMsg.value = (typeof d === 'string' && d) ? d : (t('agent.loadFailed') || '').replace('{error}', '')
    agentSecretMsgType.value = 'err'
    return ''
  } finally {
    agentSecretBusy.value = false
  }
}

// 复制 secret 到剪贴板；尚未拉取明文则先拉取再复制（初次/重置后触发）
async function copyAgentSecret() {
  if (agentSecretBusy.value) return
  let secret = agentForm.secret
  if (!agentSecretVisible.value || !secret) {
    secret = await fetchAgentSecret()
  }
  if (!secret) return
  const ok = await copyTextToClipboard(secret)
  agentSecretMsg.value = ok ? t('agent.copySuccess') : (t('agent.copyFailed') || '').replace('{error}', '')
  agentSecretMsgType.value = ok ? 'ok' : 'err'
}

// 眼睛按钮：切换 secret 明文可见。框内无明文但后端存有可展示 secret（初次/重置后）
// 时，先拉取一次明文再显示；其余情况仅切换当前输入框明文/密文显示。
async function toggleAgentSecret() {
  if (agentSecretBusy.value) return
  if (agentSecretVisible.value) {
    agentSecretVisible.value = false
    return
  }
  if (!agentForm.secret && agentStatus.has_secret) {
    await fetchAgentSecret()
  } else {
    agentSecretVisible.value = true
  }
}

async function loadAgentCfg() {
  try {
    const s = await agentApi.status()
    agentStatus.enabled = !!s.enabled
    agentStatus.has_secret = !!s.has_secret
    Object.assign(agentForm, {
      enabled: !!s.enabled,
      key: s.key || '',
      secret: '', // 明文不回显，留空表示保持原值
    })
    agentMsg.value = ''
  } catch (e) {
    // 非管理员 / 接口异常时静默，不阻塞设置窗口
  }
}

// 生成随机成对密钥并将「启用」打开，方便快速接入
function genAgentKey() {
  const rand = (chars) => {
    const bytes = new Uint8Array(chars)
    if (window.crypto && window.crypto.getRandomValues) {
      window.crypto.getRandomValues(bytes)
      // 转 URL-safe 字符：每字节扩成 base64url 片段，保证无特殊符号
      return Array.from(bytes, (b) =>
        'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_'[b % 64]
      ).join('')
    }
    // 旧浏览器兜底：用 Math.random 拼串
    const pool = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_'
    return Array.from({ length: chars }, () => pool[Math.floor(Math.random() * pool.length)]).join('')
  }
  agentForm.key = rand(16)
  agentForm.secret = rand(32)
  agentForm.enabled = true
  agentMsg.value = ''
}

async function saveAgentCfg() {
  if (agentSaving.value) return
  if (agentForm.enabled) {
    if (!agentForm.key.trim()) {
      agentMsg.value = t('agent.keyRequired')
      agentMsgType.value = 'err'
      return
    }
    // 首次启用 / 需更换 secret 时必须非空；「已配置过再编辑」可留空保持原值
    if (!agentForm.secret.trim() && !agentStatus.has_secret) {
      agentMsg.value = t('agent.secretRequired')
      agentMsgType.value = 'err'
      return
    }
  }
  agentSaving.value = true
  agentMsg.value = ''
  try {
    const res = await agentApi.save({
      enabled: !!agentForm.enabled,
      key: agentForm.key.trim(),
      secret: agentForm.secret.trim(), // 后端留空保持原值
    })
    agentStatus.enabled = !!res.enabled
    agentStatus.has_secret = !!res.has_secret
    agentForm.secret = '' // 保存后清空，避免编辑区残留明文
    // 初次/重置后 can_reveal 为 true → 自动拉取明文展示一次，便于复制到其它面板
    agentSecretVisible.value = false
    if (res.can_reveal) {
      await fetchAgentSecret()
    } else {
      agentSecretMsg.value = ''
    }
    agentMsg.value = t('agent.saved')
    agentMsgType.value = 'ok'
  } catch (e) {
    const d = e?.response?.data?.detail
    agentMsg.value = (typeof d === 'string' && d) ? d : (t('agent.saveFailed') || '').replace('{error}', '')
    agentMsgType.value = 'err'
  } finally {
    agentSaving.value = false
  }
}

const statusText = computed(() => {
  if (!currentEntry.value) return t('settings.shunxNotSet')
  return t('settings.shunxEnabled', { path: currentEntry.value })
})

// ---- 两步验证（2FA）----
const otpState = reactive({ enabled: false, has_secret: false })
const otpSecret = ref('')
const otpUri = ref('')
const otpUriQrFail = ref(false)
const otpCode = ref('')
const otpBusy = ref(false)
const otpMsg = ref('')
const otpMsgType = ref('')

// 二维码用外部公共服务生成（失败时回退到手动输入密钥，不影响功能）
function qrUrl(uri) {
  return `https://api.qrserver.com/v1/create-qr-code/?size=240x240&data=${encodeURIComponent(uri)}`
}

async function loadOtpStatus() {
  try {
    const st = await authApi.me2faStatus()
    otpState.enabled = !!st.otp_enabled
    otpState.has_secret = !!st.has_secret
    otpMsg.value = ''
  } catch (e) {
    // 接口异常时静默，不阻塞设置窗口
  }
}

async function otpSetup() {
  otpBusy.value = true
  otpMsg.value = ''
  otpUriQrFail.value = false
  try {
    const r = await authApi.twoFaSetup()
    otpSecret.value = r.secret
    otpUri.value = r.otpauth_uri
    otpState.has_secret = true
    otpCode.value = ''
  } catch (e) {
    otpMsg.value = e?.response?.data?.detail || '生成失败'
    otpMsgType.value = 'err'
  } finally {
    otpBusy.value = false
  }
}

async function otpEnable() {
  if (otpBusy.value) return
  otpBusy.value = true
  otpMsg.value = ''
  try {
    await authApi.twoFaEnable(otpCode.value)
    otpState.enabled = true
    otpSecret.value = ''
    otpUri.value = ''
    otpCode.value = ''
    otpMsg.value = '两步验证已启用'
    otpMsgType.value = 'ok'
  } catch (e) {
    otpMsg.value = e?.response?.data?.detail || '启用失败'
    otpMsgType.value = 'err'
  } finally {
    otpBusy.value = false
  }
}

async function otpDisable() {
  if (otpBusy.value) return
  otpBusy.value = true
  otpMsg.value = ''
  try {
    await authApi.twoFaDisable(otpCode.value)
    otpState.enabled = false
    otpState.has_secret = false
    otpCode.value = ''
    otpMsg.value = '两步验证已关闭'
    otpMsgType.value = 'ok'
  } catch (e) {
    otpMsg.value = e?.response?.data?.detail || '关闭失败'
    otpMsgType.value = 'err'
  } finally {
    otpBusy.value = false
  }
}

// ---- 回收站设置（仅管理员） ----
const rcForm = reactive({ enabled: true, autoDelete: true, days: 30 })
const rcSaving = ref(false)
const rcMsg = ref('')
const rcMsgType = ref('')

async function loadRecycle() {
  try {
    const cfg = await recycleApi.config()
    rcForm.enabled = !!cfg.enabled
    rcForm.autoDelete = !!cfg.auto_delete
    rcForm.days = cfg.auto_delete_days || 30
  } catch (e) {
    // 接口异常时静默，不阻塞设置窗口其它功能
  }
}

async function saveRecycle() {
  if (rcSaving.value) return
  // 表单校验：保留天数限制 1-365
  const days = rcForm.days
  if (!days || days < 1 || days > 365 || !Number.isInteger(days)) {
    rcMsg.value = t('recycle.daysInvalid')
    rcMsgType.value = 'err'
    return
  }
  rcSaving.value = true
  rcMsg.value = ''
  try {
    await recycleApi.saveConfig({ enabled: !!rcForm.enabled, auto_delete: !!rcForm.autoDelete, auto_delete_days: days })
    rcMsg.value = t('recycle.saved')
    rcMsgType.value = 'ok'
  } catch (e) {
    const d = e?.response?.data?.detail
    rcMsg.value = (typeof d === 'string' && d) ? d : t('recycle.saveFailed', { error: e?.message || '' })
    rcMsgType.value = 'err'
  } finally {
    rcSaving.value = false
  }
}

// ---- 关于：项目与社区链接 ----
// nameKey 为多语言键，url 为固定外链；集中在此便于维护与扩展
const aboutLinks = [
  { key: 'donate', nameKey: 'Graw Web', url: 'https://graw.shunx.top/' },
  { key: 'donate', nameKey: 'settings.about.donate', url: 'https://ifdian.net/a/shunianssy' },
  { key: 'github', nameKey: 'settings.about.githubSource', url: 'https://github.com/wuhulab/Graw' },
  { key: 'docker', nameKey: 'settings.about.docker', url: 'https://hub.docker.com/repository/docker/shunx/graw/general' },
  { key: 'wuhulab', nameKey: 'settings.about.wuhulab', url: 'https://github.com/wuhulab/' },
  { key: 'appstore', nameKey: 'settings.about.appStore', url: 'https://github.com/wuhulab/Graw-app-store' },
  { key: 'sponsor', nameKey: 'settings.about.sponsorFai', url: 'https://fai.shunx.top/' },
  { key: 'qq1', nameKey: 'settings.about.qqYearnstudio', url: 'https://qm.qq.com/cgi-bin/qm/qr?k=tBGCIw9wWxxvR8Y37HzQYVu6IXA6ewCf&jump_from=webapi&authKey=IyaUqb5UDh/VFbNJ4YGEOMChPr6HUpGBeBzz8zweQeFHV8RsiHFiK4xJ1IXR/Y1x' },
  { key: 'qq2', nameKey: 'settings.about.qqSbox', url: 'https://qm.qq.com/cgi-bin/qm/qr?k=qMHdqob8wFPfeKNjWCgVB2k3EQD90KaL&jump_from=webapi&authKey=k0gFk6S1kjJFYSYzDU9pCFjpNdCmjGvaAAABo2WOuH/lKMGonWwXkqMFNDn0mVov' },
  { key: 'qq3', nameKey: 'settings.about.qqFox', url: 'https://qm.qq.com/cgi-bin/qm/qr?k=0BEct4NXBJ9b628GLej_4_W9W4KBvOXk&jump_from=webapi&authKey=UJWc1dTVUj98iwOadM5fOw7tP2+s/xnN1oG1JiOlMUvjWCYFgwK2ygXIEaYq6uen' },
  { key: 'shunx', nameKey: 'settings.about.shunxTeam', url: 'https://www.shunx.top/' },
  { key: 'bili', nameKey: 'settings.about.bilibili', url: 'https://space.bilibili.com/3546925812943471' },
  { key: 'bili2', nameKey: 'settings.about.bilibili2', url: 'https://space.bilibili.com/3493133419546943' },
  { key: 'contributor', nameKey: 'settings.about.contributor', url: 'https://github.com/shunianssy' },
]

// 面板版本号：来自公开接口 /api/health，加载失败时静默留空（不阻塞页面）
const panelVersion = ref('')
async function loadVersion() {
  try {
    const res = await panelApi.health()
    panelVersion.value = res.version || ''
  } catch (e) {
    // 版本号获取失败不影响设置窗口其它功能，仅隐藏版本行
    panelVersion.value = ''
  }
}

// ---- 面板版本更新检测与一键更新 ----
// 仅管理员可更新（后端 /api/update 挂 ADMIN 依赖）；检测失败时静默隐藏更新区
const updateAvailable = ref(false)
const latestVersion = ref('')
const updateChecked = ref(false) // 是否已完成版本检测（区分「未检测」与「已是最新」）
const updating = ref(false)
const updateMsg = ref('')
const updateMsgType = ref('')

async function loadUpdateStatus() {
  try {
    const res = await updateApi.status()
    updateAvailable.value = !!res.update_available
    latestVersion.value = res.latest_version || ''
    updateChecked.value = true
  } catch (e) {
    // 网络/权限异常（如非管理员）时静默，不显示任何更新提示
    updateAvailable.value = false
    updateChecked.value = false
  }
}

async function doUpdate() {
  if (updating.value) return
  if (!confirm(t('settings.about.updateConfirm', { version: latestVersion.value }))) return
  updating.value = true
  updateMsg.value = ''
  try {
    const res = await updateApi.apply()
    updateMsg.value = res.message || t('settings.about.updateStarted')
    updateMsgType.value = 'ok'
  } catch (e) {
    updateMsg.value = e?.response?.data?.detail || t('settings.about.updateFailed')
    updateMsgType.value = 'err'
  } finally {
    updating.value = false
  }
}

// ---- 多机（多节点）管理 ----
const nodesList = ref([])
const currentId = ref('local')
const loadingNodes = ref(false)
const nodeLoadError = ref('')
const connMsg = ref('')
const connMsgType = ref('')
const current = computed(() => {
  const cur = nodesList.value.find((n) => n.id === currentId.value)
  return cur || { id: 'local', name: 'local', type: 'local' }
})
const showEditor = ref(false)
const editingId = ref(null)
const form = reactive({ id: '', name: '', host: '', port: 22, user: '', auth: 'password', password: '', key_path: '', agent_enabled: false, agent_port: 8000, agent_key: '', agent_secret: '' })
const savingNode = ref(false)
const testingId = ref('')
const editorMsg = ref('')
const editorMsgType = ref('')

async function loadNodes() {
  if (loadingNodes.value) return
  loadingNodes.value = true
  nodeLoadError.value = ''
  try {
    await refreshNodes()
    nodesList.value = nodesStore.list
    currentId.value = nodesStore.currentId
  } catch (e) {
    nodeLoadError.value = t('nodes.loadFailed', { error: e?.response?.data?.detail || e.message })
  } finally {
    loadingNodes.value = false
  }
}

function startAdd() {
  editingId.value = null
  Object.assign(form, { id: '', name: '', host: '', port: 22, user: '', auth: 'password', password: '', key_path: '', agent_enabled: false, agent_port: 8000, agent_key: '', agent_secret: '' })
  editorMsg.value = ''
  showEditor.value = true
}

function startEdit(n) {
  editingId.value = n.id
  // 密钥/校验 secret 出于安全不回传列表，编辑时留空表示「保持原值」
  Object.assign(form, {
    id: n.id,
    name: n.name,
    host: n.host,
    port: n.port,
    user: n.user,
    auth: n.auth,
    password: '',
    key_path: n.key_path || '',
    agent_enabled: !!n.agent_enabled,
    agent_port: n.agent_port || 8000,
    agent_key: '',
    agent_secret: '',
  })
  editorMsg.value = ''
  showEditor.value = true
}

function cancelEdit() {
  showEditor.value = false
  editorMsg.value = ''
}

async function saveNode() {
  if (savingNode.value) return
  const name = (form.name || '').trim()
  const host = (form.host || '').trim()
  const user = (form.user || '').trim()
  if (!name) return editorError(t('nodes.nameRequired'))
  if (!host) return editorError(t('nodes.hostRequired'))
  if (!user) return editorError(t('nodes.userRequired'))
  if (form.auth === 'key' && !(form.key_path || '').trim()) return editorError(t('nodes.keyRequired'))
  if (form.agent_enabled) {
    if (!(form.agent_key || '').trim()) return editorError(t('nodes.agentKeyRequired'))
    // agent_secret：新增必须填；编辑可留空表示保持原值
    if (!editingId.value && !(form.agent_secret || '').trim()) return editorError(t('nodes.agentSecretRequired'))
  }
  editorMsg.value = ''
  editorMsgType.value = ''
  savingNode.value = true
  try {
    const body = {
      name,
      host,
      port: form.port || 22,
      user,
      auth: form.auth,
      password: form.password || '',
      key_path: (form.key_path || '').trim(),
      agent_port: form.agent_port || 8000,
      agent_key: form.agent_enabled ? (form.agent_key || '').trim() : '',
      agent_secret: form.agent_enabled ? (form.agent_secret || '').trim() : '',
      agent_enabled: !!form.agent_enabled,
    }
    if (editingId.value) {
      await nodesApi.update(editingId.value, body)
    } else {
      await nodesApi.create(body)
    }
    await loadNodes()
    showEditor.value = false
    editorMsg.value = t('nodes.saved')
    editorMsgType.value = 'ok'
  } catch (e) {
    editorError(t('nodes.saveFailed', { error: e?.response?.data?.detail || e.message }))
  } finally {
    savingNode.value = false
  }
}

function editorError(msg) {
  editorMsg.value = msg
  editorMsgType.value = 'err'
  return false
}

async function switchNode(n) {
  if (n.id === currentId.value) return
  try {
    await setCurrentNode(n.id)
    currentId.value = nodesStore.currentId
    editorMsg.value = t('nodes.switched', { name: n.name })
    editorMsgType.value = 'ok'
  } catch (e) {
    editorError(t('nodes.switchFailed', { error: e?.response?.data?.detail || e.message }))
  }
}

async function testNode(n) {
  if (testingId.value) return
  testingId.value = n.id
  connMsg.value = ''
  connMsgType.value = ''
  try {
    const res = await nodesApi.test(n.id)
    connMsg.value = res.ok ? t('nodes.testOk') : t('nodes.testFail', { error: res.message || '' })
    connMsgType.value = res.ok ? 'ok' : 'err'
  } catch (e) {
    connMsg.value = t('nodes.testFail', { error: e?.response?.data?.detail || e.message })
    connMsgType.value = 'err'
  } finally {
    testingId.value = ''
  }
}

function removeNode(n) {
  // 高风险操作：删除远程节点需输入面板密码确认后才真正执行
  dangerConfirm.value = {
    show: true,
    mode: 'password',
    title: t('confirmDanger.deleteNodeTitle'),
    message: t('confirmDanger.deleteNodeMsg', { name: n.name }),
    requiredText: '',
    inputLabel: t('confirmDanger.inputPwdLabel'),
    placeholder: t('confirmDanger.inputPwdPlaceholder'),
    confirmLabel: t('common.delete'),
    action: { type: 'node', node: n }
  }
}

onMounted(() => {
  // 并行加载各区块数据：多机管理、Web 模式等不再被前置串行请求阻塞
  loadVersion()
  loadUpdateStatus()
  loadOtpStatus()
  // ShunX 安全入口配置（自身独立加载，失败不阻塞其它区块）
  shunxApi.config()
    .then((config) => {
      currentEntry.value = config.entry_path || ''
      entryPath.value = currentEntry.value
    })
    .catch(() => {
      currentEntry.value = ''
    })
  if (isAdmin()) loadNodes()
  if (isAdmin()) loadWebMode()
  if (isAdmin()) loadAgentCfg()
  if (isAdmin()) loadRecycle()
  // 付费功能：刷新当前账号 VIP 状态（决定「统一面板兼容」是否可解锁）
  refreshVip()
})

// 切换界面语言
function changeLocale(code) {
  setLocale(code)
}

async function saveEntry() {
  if (saving.value) return
  saving.value = true
  msg.value = ''
  try {
    const res = await shunxApi.update(entryPath.value)
    const config = res.config || {}
    currentEntry.value = config.entry_path || ''
    entryPath.value = currentEntry.value
    msg.value = currentEntry.value
      ? t('settings.entrySet', { url: `${origin.value}/${currentEntry.value}` })
      : t('settings.entryCleared')
    msgType.value = 'ok'
  } catch (e) {
    msg.value = e?.response?.data?.detail || t('settings.saveFailed')
    msgType.value = 'err'
  } finally {
    saving.value = false
  }
}

function clearEntry() {
  // 高风险配置变更：清除安全入口后任何设备可直接访问登录页，需输入面板密码确认
  dangerConfirm.value = {
    show: true,
    mode: 'password',
    title: '清除安全入口确认',
    message: '清除 ShunX 安全入口后，任何设备将可直接访问登录页面。\n请输入面板密码以确认。',
    requiredText: '',
    inputLabel: t('confirmDanger.inputPwdLabel'),
    placeholder: t('confirmDanger.inputPwdPlaceholder'),
    confirmLabel: '清除',
    action: { type: 'entry' }
  }
}

// ConfirmDialog 密码校验通过后的回调：按 action.type 真正执行高风险操作
async function doConfirm() {
  const a = dangerConfirm.value.action
  dangerConfirm.value.show = false
  if (!a) return
  if (a.type === 'node') {
    try {
      await nodesApi.delete(a.node.id)
      await loadNodes()
    } catch (e) {
      editorError(t('nodes.deleteFailed', { error: e?.response?.data?.detail || e.message }))
    }
  } else if (a.type === 'entry') {
    await clearEntryNow()
  }
}

// 真正执行清除安全入口（密码已通过校验，原 clearEntry 的业务逻辑）
async function clearEntryNow() {
  if (saving.value) return
  saving.value = true
  msg.value = ''
  try {
    await shunxApi.update('')
    currentEntry.value = ''
    entryPath.value = ''
    msg.value = t('settings.entryCleared')
    msgType.value = 'ok'
  } catch (e) {
    msg.value = e?.response?.data?.detail || t('settings.clearFailed')
    msgType.value = 'err'
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.block {
  background: #fff;
  border-radius: 10px;
  padding: 12px 14px;
  border: 1px solid rgba(0,0,0,0.06);
}
.block-title {
  font-size: 12px;
  font-weight: 600;
  color: #1d1d1f;
  margin-bottom: 10px;
}
.row {
  display: flex;
  align-items: center;
  padding: 6px 0;
  font-size: 12px;
}
.switch-label {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  color: #1d1d1f;
}
.switch-label input {
  width: 16px;
  height: 16px;
  cursor: pointer;
}
.btn {
  padding: 6px 14px;
  font-size: 12px;
  color: #fff;
  background: #0a84ff;
  border: none;
  border-radius: 8px;
  cursor: pointer;
}
.btn:hover:not(:disabled) { background: #006ee6; }
.btn:disabled { opacity: 0.6; cursor: not-allowed; }
.btn-danger {
  background: #e5484d;
}
.btn-danger:hover:not(:disabled) { background: #d63d42; }
.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
}
.status-dot.on { background: #0a7d3b; }
.status-dot.off { background: #c0392b; }
input {
  width: 100%;
  padding: 8px 10px;
  font-size: 12px;
  font-family: inherit;
  color: #1d1d1f;
  border: 1px solid rgba(0,0,0,0.12);
  border-radius: 8px;
  outline: none;
  box-sizing: border-box;
}
input:focus {
  border-color: #0a84ff;
  box-shadow: 0 0 0 3px rgba(10,132,255,0.15);
}
.msg {
  font-size: 12px;
  border-radius: 6px;
  padding: 6px 8px;
}
.msg.ok {
  color: #0a7d3b;
  background: rgba(10,132,255,0.08);
  border: 1px solid rgba(10,132,255,0.25);
}
.msg.err {
  color: #c0392b;
  background: rgba(255,59,48,0.08);
  border: 1px solid rgba(255,59,48,0.2);
}
/* ---- 多机（多节点）管理 ---- */
.node-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  background: #fafafa;
  border-radius: 8px;
  border: 1px solid rgba(0,0,0,0.05);
}
.tag {
  font-size: 10px;
  padding: 1px 7px;
  border-radius: 999px;
  flex-shrink: 0;
}
.tag-local {
  color: #0a7d3b;
  background: rgba(10,125,59,0.12);
}
.tag-remote {
  color: #0a84ff;
  background: rgba(10,132,255,0.12);
}
.tag-current {
  color: #fff;
  background: #0a84ff;
}
.tag-agenty {
  color: #7a3ce8;
  background: rgba(122,60,232,0.12);
}
.btn-mini {
  padding: 3px 10px;
  font-size: 11px;
}
.editor {
  margin-top: 10px;
  padding: 10px;
  background: #fafafa;
  border-radius: 8px;
  border: 1px solid rgba(0,0,0,0.06);
}
/* ---- 关于（About）---- */
.about-version {
  font-size: 12px;
  color: #1d1d1f;
  margin-bottom: 10px;
}
.about-version-val {
  font-weight: 600;
  color: #0a84ff;
}
/* 版本更新提示 */
.update-hint {
  margin-left: 6px;
  font-size: 11px;
  color: #e5484d;
}
.update-latest {
  margin-left: 6px;
  font-size: 11px;
  color: #0a7d3b;
}
.btn-update {
  margin-left: 8px;
  padding: 3px 12px;
  font-size: 11px;
  background: #e5484d;
}
.btn-update:hover:not(:disabled) { background: #d63d42; }
.btn-update:disabled { opacity: 0.6; cursor: not-allowed; }
.about-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.about-link {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 10px;
  background: #fafafa;
  border: 1px solid rgba(0,0,0,0.05);
  border-radius: 8px;
  text-decoration: none;
  color: #1d1d1f;
  transition: background 0.15s;
}
.about-link:hover {
  background: rgba(10,132,255,0.06);
  border-color: rgba(10,132,255,0.25);
}
.about-name {
  font-size: 12px;
  font-weight: 500;
  flex-shrink: 0;
}
.about-url {
  font-size: 11px;
  color: #0a84ff;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 60%;
}
</style>
