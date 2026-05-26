;(function () {
	"use strict"

	var SDK = window.__HERMES_PLUGIN_SDK__
	if (!SDK || !window.__HERMES_PLUGINS__) return

	var React = SDK.React
	var hooks = SDK.hooks
	var useState = hooks.useState
	var useEffect = hooks.useEffect
	var useCallback = hooks.useCallback

	var C = SDK.components
	var Card = C.Card
	var CardContent = C.CardContent

	var sessionToken = window.__HERMES_SESSION_TOKEN__
	var basePath = (window.__HERMES_BASE_PATH__ || "").replace(/\/$/, "")

	function api(path, options) {
		var opts = options || {}
		var headers = new Headers(opts.headers)
		if (sessionToken && !headers.has("X-Hermes-Session-Token")) {
			headers.set("X-Hermes-Session-Token", sessionToken)
		}
		return fetch(basePath + "/api/plugins/workflow" + path, {
			method: opts.method || "GET",
			headers: headers,
			body: opts.body,
		}).then(function (res) {
			if (!res.ok) throw new Error(res.status + ": " + res.statusText)
			return res.json()
		})
	}

	function fmtTime(ts) {
		if (!ts) return "\u2014"
		try { return new Date(ts * 1000).toLocaleString() } catch (e) { return String(ts) }
	}

	function truncate(str, max) {
		if (!str) return "\u2014"
		return str.length > max ? str.slice(0, max) + "\u2026" : str
	}

	// ── Stats bar ──────────────────────────────────────────────────────

	function StatsBar(props) {
		var s = props.stats
		if (!s) return null
		var badge = "inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold"
		return React.createElement("div", { className: "flex flex-wrap gap-3 mb-4" },
			React.createElement("span", { className: badge }, s.pending_workflows + " pending"),
			React.createElement("span", { className: badge }, s.resolved_workflows + " resolved"),
			React.createElement("span", { className: badge }, s.state_keys + " state keys"),
		)
	}

	// ── Pending workflow card ──────────────────────────────────────────

	function PendingCard(props) {
		var action = props.action
		var onRefresh = props.onRefresh

		var _s1 = useState(""), responseText = _s1[0], setResponseText = _s1[1]
		var _s2 = useState(false), submitting = _s2[0], setSubmitting = _s2[1]
		var _s3 = useState(false), showResponse = _s3[0], setShowResponse = _s3[1]

		var respond = useCallback(function () {
			if (!responseText.trim()) return
			setSubmitting(true)
			api("/respond", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ workflow_id: action.workflow_id, response: responseText.trim() }) })
				.then(function () { setResponseText(""); setShowResponse(false); onRefresh() })
				.catch(function (e) { console.error(e) })
				.finally(function () { setSubmitting(false) })
		}, [action.workflow_id, responseText, onRefresh])

		var dismiss = useCallback(function () {
			setSubmitting(true)
			api("/dismiss", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ workflow_id: action.workflow_id }) })
				.then(function () { onRefresh() })
				.catch(function (e) { console.error(e) })
				.finally(function () { setSubmitting(false) })
		}, [action.workflow_id, onRefresh])

		var ctx = null
		if (action.context) { try { ctx = JSON.parse(action.context) } catch (e) {} }

		return React.createElement(Card, { className: "mb-3" },
			React.createElement("div", { className: "p-4 pb-2" },
				React.createElement("div", { className: "flex items-start justify-between gap-2" },
					React.createElement("div", { className: "min-w-0 flex-1" },
						React.createElement("div", { className: "text-sm font-mono font-semibold break-all" }, action.workflow_id),
						action.cron_job_id ? React.createElement("div", { className: "text-xs text-text-tertiary mt-0.5" }, "Cron: ", React.createElement("code", null, action.cron_job_id)) : null,
					),
					React.createElement("span", { className: "inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-semibold text-amber-400 border-amber-500/30" }, "pending"),
				),
				React.createElement("p", { className: "text-sm text-text-secondary mt-2" }, action.question),
			),
			React.createElement(CardContent, { className: "pt-0" },
				ctx ? React.createElement("pre", { className: "text-xs bg-bg-tertiary rounded p-2 mb-3 overflow-x-auto max-h-32" }, JSON.stringify(ctx, null, 2))
					: action.context ? React.createElement("p", { className: "text-xs text-text-tertiary mb-3" }, truncate(action.context, 200)) : null,
				React.createElement("div", { className: "text-xs text-text-tertiary mb-3" }, "Created: " + fmtTime(action.created_at)),
				showResponse
					? React.createElement("div", { className: "space-y-2" },
						React.createElement("label", { className: "text-sm font-medium" }, "Your response:"),
						React.createElement("input", { className: "w-full rounded border border-border bg-bg-secondary px-3 py-2 text-sm", value: responseText, onChange: function (e) { setResponseText(e.target.value) }, placeholder: "Type your answer..." }),
						React.createElement("div", { className: "flex gap-2" },
							React.createElement("button", { disabled: submitting || !responseText.trim(), onClick: respond, className: "inline-flex items-center rounded bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground disabled:opacity-50" }, submitting ? "Sending\u2026" : "Submit Response"),
							React.createElement("button", { onClick: function () { setShowResponse(false); setResponseText("") }, className: "inline-flex items-center rounded px-3 py-1.5 text-xs" }, "Cancel"),
						),
					)
					: React.createElement("div", { className: "flex gap-2" },
						React.createElement("button", { onClick: function () { setShowResponse(true) }, className: "inline-flex items-center rounded bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground" }, "Respond"),
						React.createElement("button", { onClick: dismiss, disabled: submitting, className: "inline-flex items-center rounded px-3 py-1.5 text-xs disabled:opacity-50" }, "Dismiss"),
					),
			),
		)
	}

	// ── Resolved workflow card ─────────────────────────────────────────

	function ResolvedCard(props) {
		var a = props.action
		return React.createElement(Card, { className: "mb-3 opacity-70" },
			React.createElement("div", { className: "p-4 pb-2" },
				React.createElement("div", { className: "flex items-start justify-between gap-2" },
					React.createElement("div", { className: "text-sm font-mono font-semibold break-all" }, a.workflow_id),
					React.createElement("span", { className: "inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-semibold" }, "resolved"),
				),
				React.createElement("p", { className: "text-sm text-text-secondary mt-2" }, a.question),
			),
			React.createElement(CardContent, { className: "pt-0" },
				React.createElement("div", { className: "bg-bg-tertiary rounded p-2 mb-2" },
					React.createElement("span", { className: "text-xs font-semibold text-text-secondary" }, "Response: "),
					React.createElement("span", { className: "text-sm" }, a.response || "\u2014"),
				),
				React.createElement("div", { className: "text-xs text-text-tertiary" }, "Created: " + fmtTime(a.created_at) + " \u00b7 Resolved: " + fmtTime(a.responded_at)),
			),
		)
	}

	// ── State keys panel ───────────────────────────────────────────────

	function StatePanel(props) {
		var keys = props.keys || []
		var onRefresh = props.onRefresh
		var _s4 = useState(null), expandedKey = _s4[0], setExpandedKey = _s4[1]
		var _s5 = useState(null), keyValue = _s5[0], setKeyValue = _s5[1]

		var toggle = useCallback(function (key) {
			if (expandedKey === key) { setExpandedKey(null); setKeyValue(null); return }
			setExpandedKey(key)
			api("/state/" + encodeURIComponent(key)).then(function (d) { setKeyValue(d.value) }).catch(function () { setKeyValue("(failed to load)") })
		}, [expandedKey])

		var del = useCallback(function (key) {
			api("/state/delete", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ key: key }) })
				.then(function () { setExpandedKey(null); setKeyValue(null); onRefresh() })
				.catch(function (e) { console.error(e) })
		}, [onRefresh])

		if (keys.length === 0) return React.createElement("p", { className: "text-sm text-text-tertiary py-4 text-center" }, "No state keys stored yet.")

		return React.createElement("div", { className: "space-y-1" },
			keys.map(function (key) {
				var open = expandedKey === key
				return React.createElement("div", { key: key, className: "border border-border rounded" },
					React.createElement("div", { className: "flex items-center justify-between p-2 cursor-pointer hover:bg-bg-tertiary", onClick: function () { toggle(key) } },
						React.createElement("code", { className: "text-xs" }, key),
						React.createElement("button", { onClick: function (e) { e.stopPropagation(); del(key) }, className: "inline-flex items-center rounded px-2 py-1 text-xs text-red-400 hover:bg-red-500/10" }, "Delete"),
					),
					open ? React.createElement("pre", { className: "text-xs bg-bg-tertiary p-2 m-2 rounded overflow-x-auto max-h-64" }, keyValue === null ? "Loading\u2026" : typeof keyValue === "string" ? keyValue : JSON.stringify(keyValue, null, 2)) : null,
				)
			}),
		)
	}

	// ── Main page ──────────────────────────────────────────────────────

	function WorkflowPage() {
		var _t = useState("pending"), tab = _t[0], setTab = _t[1]
		var _d1 = useState(null), pendingData = _d1[0], setPendingData = _d1[1]
		var _d2 = useState(null), stateData = _d2[0], setStateData = _d2[1]
		var _d3 = useState(null), statsData = _d3[0], setStatsData = _d3[1]
		var _l = useState(true), loading = _l[0], setLoading = _l[1]

		var loadAll = useCallback(function () {
			setLoading(true)
			Promise.all([api("/pending"), api("/state"), api("/stats")])
				.then(function (r) { setPendingData(r[0]); setStateData(r[1]); setStatsData(r[2]) })
				.catch(function (e) { console.error("Workflow Engine: failed to load data", e) })
				.finally(function () { setLoading(false) })
		}, [])

		useEffect(function () { loadAll() }, [loadAll])

		var pending = (pendingData && pendingData.pending) || []
		var resolved = (pendingData && pendingData.resolved) || []
		var keys = (stateData && stateData.keys) || []

		if (loading) {
			return React.createElement("div", { className: "flex items-center justify-center py-12" },
				React.createElement("div", { className: "inline-block h-6 w-6 animate-spin rounded-full border-2 border-muted border-t-primary", role: "status" },
					React.createElement("span", { className: "sr-only" }, "Loading\u2026"),
				),
			)
		}

		var tabs = [
			{ id: "pending", label: "Pending", count: pending.length },
			{ id: "resolved", label: "Resolved", count: resolved.length },
			{ id: "state", label: "State", count: keys.length },
		]

		return React.createElement("div", { className: "max-w-3xl mx-auto" },
			React.createElement("div", { className: "flex items-center justify-between mb-4" },
				React.createElement("h1", { className: "text-lg font-semibold tracking-tight" }, "Workflow Engine"),
				React.createElement("button", { onClick: loadAll, className: "inline-flex items-center rounded px-3 py-1.5 text-xs hover:bg-bg-tertiary" }, "Refresh"),
			),
			React.createElement(StatsBar, { stats: statsData }),

			React.createElement("div", { className: "flex gap-1 mb-3 border-b border-border" },
				tabs.map(function (t) {
					return React.createElement("button", {
						key: t.id, onClick: function () { setTab(t.id) },
						className: tab === t.id ? "px-3 py-2 text-sm font-medium border-b-2 border-primary text-primary -mb-px" : "px-3 py-2 text-sm text-text-tertiary hover:text-text-secondary border-b-2 border-transparent",
					}, t.label + " (" + t.count + ")")
				}),
			),

			tab === "pending" && React.createElement("div", null,
				pending.length === 0 ? React.createElement("p", { className: "text-sm text-text-tertiary py-8 text-center" }, "No pending workflows.")
					: pending.map(function (a) { return React.createElement(PendingCard, { key: a.workflow_id, action: a, onRefresh: loadAll }) }),
			),
			tab === "resolved" && React.createElement("div", null,
				resolved.length === 0 ? React.createElement("p", { className: "text-sm text-text-tertiary py-8 text-center" }, "No resolved workflows yet.")
					: resolved.map(function (a) { return React.createElement(ResolvedCard, { key: a.workflow_id, action: a }) }),
			),
			tab === "state" && React.createElement(StatePanel, { keys: keys, onRefresh: loadAll }),
		)
	}

	window.__HERMES_PLUGINS__.register("workflow", WorkflowPage)

	window.__HERMES_PLUGINS__.registerSlot("workflow", "cron:bottom", function () {
		var _s = useState(null), stats = _s[0], setStats = _s[1]
		useEffect(function () { api("/stats").then(setStats).catch(function () {}) }, [])
		if (!stats || stats.pending_workflows === 0) return null
		return React.createElement("div", { className: "mt-3 rounded border border-amber-500/30 bg-amber-500/10 p-3" },
			React.createElement("p", { className: "text-xs text-amber-400 font-semibold" },
				"\u23F3 " + stats.pending_workflows + " workflow(s) awaiting your response. ",
				React.createElement("a", { href: "#/workflows", className: "underline" }, "Review \u2192"),
			),
		)
	})
})()
