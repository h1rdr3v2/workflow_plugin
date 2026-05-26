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
	var CardHeader = C.CardHeader
	var CardTitle = C.CardTitle
	var CardContent = C.CardContent
	var Badge = C.Badge
	var Button = C.Button
	var Tabs = C.Tabs
	var TabsList = C.TabsList
	var TabsTrigger = C.TabsTrigger
	var Separator = C.Separator

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
		return React.createElement("div", { className: "flex flex-wrap gap-3 mb-4" },
			React.createElement(Badge, null, s.pending_workflows + " pending"),
			React.createElement(Badge, null, s.resolved_workflows + " resolved"),
			React.createElement(Badge, null, s.state_keys + " state keys"),
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
			React.createElement(CardHeader, { className: "pb-2" },
				React.createElement("div", { className: "flex items-start justify-between gap-2" },
					React.createElement("div", { className: "min-w-0 flex-1" },
						React.createElement(CardTitle, { className: "text-sm font-mono break-all" }, action.workflow_id),
						action.cron_job_id ? React.createElement("div", { className: "text-xs text-text-tertiary mt-0.5" }, "Cron: ", React.createElement("code", null, action.cron_job_id)) : null,
					),
					React.createElement(Badge, null, "pending"),
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
						React.createElement("input", {
							className: "w-full rounded border border-border bg-bg-secondary px-3 py-2 text-sm",
							value: responseText, onChange: function (e) { setResponseText(e.target.value) },
							placeholder: "Type your answer, decision, or instructions\u2026",
						}),
						React.createElement("div", { className: "flex gap-2" },
							React.createElement(Button, { disabled: submitting || !responseText.trim(), onClick: respond, size: "sm" }, submitting ? "Sending\u2026" : "Submit Response"),
							React.createElement(Button, { ghost: true, size: "sm", onClick: function () { setShowResponse(false); setResponseText("") } }, "Cancel"),
						),
					)
					: React.createElement("div", { className: "flex gap-2" },
						React.createElement(Button, { size: "sm", onClick: function () { setShowResponse(true) } }, "Respond"),
						React.createElement(Button, { ghost: true, size: "sm", onClick: dismiss, disabled: submitting }, "Dismiss"),
					),
			),
		)
	}

	// ── Resolved workflow card ─────────────────────────────────────────

	function ResolvedCard(props) {
		var a = props.action
		return React.createElement(Card, { className: "mb-3 opacity-70" },
			React.createElement(CardHeader, { className: "pb-2" },
				React.createElement("div", { className: "flex items-start justify-between gap-2" },
					React.createElement(CardTitle, { className: "text-sm font-mono break-all" }, a.workflow_id),
					React.createElement(Badge, null, "resolved"),
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

		if (keys.length === 0) {
			return React.createElement("p", { className: "text-sm text-text-tertiary py-4 text-center" }, "No state keys stored yet.")
		}

		return React.createElement("div", { className: "space-y-1" },
			keys.map(function (key) {
				var open = expandedKey === key
				return React.createElement("div", { key: key, className: "border border-border rounded" },
					React.createElement("div", { className: "flex items-center justify-between p-2 cursor-pointer hover:bg-bg-tertiary", onClick: function () { toggle(key) } },
						React.createElement("code", { className: "text-xs" }, key),
						React.createElement(Button, { ghost: true, size: "sm", onClick: function (e) { e.stopPropagation(); del(key) } }, "Delete"),
					),
					open ? React.createElement("pre", { className: "text-xs bg-bg-tertiary p-2 m-2 rounded overflow-x-auto max-h-64" },
						keyValue === null ? "Loading\u2026" : typeof keyValue === "string" ? keyValue : JSON.stringify(keyValue, null, 2)) : null,
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

		return React.createElement("div", { className: "max-w-3xl mx-auto" },
			React.createElement("div", { className: "flex items-center justify-between mb-4" },
				React.createElement("h1", { className: "text-lg font-semibold tracking-tight" }, "Workflow Engine"),
				React.createElement(Button, { size: "sm", ghost: true, onClick: loadAll }, "Refresh"),
			),
			React.createElement(StatsBar, { stats: statsData }),

			React.createElement(Tabs, { value: tab, onChange: setTab },
				React.createElement(TabsList, null,
					React.createElement(TabsTrigger, { value: "pending" }, "Pending (" + pending.length + ")"),
					React.createElement(TabsTrigger, { value: "resolved" }, "Resolved (" + resolved.length + ")"),
					React.createElement(TabsTrigger, { value: "state" }, "State (" + keys.length + ")"),
				),
				React.createElement(Separator, { className: "my-3" }),
				tab === "pending" && React.createElement("div", null,
					pending.length === 0
						? React.createElement("p", { className: "text-sm text-text-tertiary py-8 text-center" }, "No pending workflows.")
						: pending.map(function (a) { return React.createElement(PendingCard, { key: a.workflow_id, action: a, onRefresh: loadAll }) }),
				),
				tab === "resolved" && React.createElement("div", null,
					resolved.length === 0
						? React.createElement("p", { className: "text-sm text-text-tertiary py-8 text-center" }, "No resolved workflows yet.")
						: resolved.map(function (a) { return React.createElement(ResolvedCard, { key: a.workflow_id, action: a }) }),
				),
				tab === "state" && React.createElement(StatePanel, { keys: keys, onRefresh: loadAll }),
			),
		)
	}

	window.__HERMES_PLUGINS__.register("workflow", WorkflowPage)

	// Cron page slot
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
