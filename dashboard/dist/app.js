/**
 * Workflow Engine — Dashboard UI Plugin.
 *
 * Plain IIFE (no build step required). Uses the Hermes Plugin SDK
 * exposed on window.__HERMES_PLUGIN_SDK__ for React and UI components.
 *
 * Registers a tab at /workflows that shows:
 *  - Pending workflows (with respond/dismiss buttons)
 *  - Resolved workflows
 *  - Workflow state keys
 *  - Summary stats
 */
;(function () {
	"use strict"

	var SDK = window.__HERMES_PLUGIN_SDK__

	// Guard: if the SDK or plugin registry isn't ready, bail silently.
	// The dashboard retries plugin loads on navigation.
	if (!SDK || !window.__HERMES_PLUGINS__) return

	var React = SDK.React
	var hooks = SDK.hooks
	var useState = hooks.useState
	var useEffect = hooks.useEffect
	var useCallback = hooks.useCallback

	// Use the same component access pattern as verified working plugins.
	var C = SDK.components
	var Card = C.Card
	var CardHeader = C.CardHeader
	var CardTitle = C.CardTitle
	var CardContent = C.CardContent
	var Badge = C.Badge
	var Button = C.Button
	var Input = C.Input
	var Label = C.Label
	var Tabs = C.Tabs
	var TabsList = C.TabsList
	var TabsTrigger = C.TabsTrigger
	var Separator = C.Separator
	// NOTE: 'Spinner' is NOT in the documented SDK — use inline loading below.

	var fetchJSON = SDK.fetchJSON
	var cn = SDK.utils.cn
	var timeAgo = SDK.utils.timeAgo

	// -----------------------------------------------------------------------
	// API helpers (calls /api/plugins/workflow/...)
	// -----------------------------------------------------------------------

	var BASE = "/api/plugins/workflow"

	function fetchPending() {
		return fetchJSON(BASE + "/pending")
	}

	function fetchState() {
		return fetchJSON(BASE + "/state")
	}

	function fetchStats() {
		return fetchJSON(BASE + "/stats")
	}

	function respondWorkflow(workflowId, response) {
		return fetchJSON(BASE + "/respond", {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({ workflow_id: workflowId, response: response }),
		})
	}

	function dismissWorkflow(workflowId) {
		return fetchJSON(BASE + "/dismiss", {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({ workflow_id: workflowId }),
		})
	}

	function deleteStateKey(key) {
		return fetchJSON(BASE + "/state/delete", {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({ key: key }),
		})
	}

	// -----------------------------------------------------------------------
	// Format helpers
	// -----------------------------------------------------------------------

	function fmtTime(ts) {
		if (!ts) return "\u2014"
		try {
			return new Date(ts * 1000).toLocaleString()
		} catch (e) {
			return String(ts)
		}
	}

	function truncate(str, max) {
		if (!str) return "\u2014"
		return str.length > max ? str.slice(0, max) + "\u2026" : str
	}

	// -----------------------------------------------------------------------
	// Sub-components
	// -----------------------------------------------------------------------

	/** Stats bar at the top. */
	function StatsBar(props) {
		var stats = props.stats
		if (!stats) return null

		return React.createElement(
			"div",
			{ className: "flex flex-wrap gap-3 mb-4" },
			React.createElement(Badge, null, stats.pending_workflows + " pending"),
			React.createElement(Badge, null, stats.resolved_workflows + " resolved"),
			React.createElement(Badge, null, stats.state_keys + " state keys"),
		)
	}

	/** A single pending workflow card. */
	function PendingCard(props) {
		var action = props.action
		var onRefresh = props.onRefresh

		var _useState = useState("")
		var responseText = _useState[0]
		var setResponseText = _useState[1]

		var _useState2 = useState(false)
		var submitting = _useState2[0]
		var setSubmitting = _useState2[1]

		var _useState3 = useState(false)
		var showResponse = _useState3[0]
		var setShowResponse = _useState3[1]

		var handleRespond = useCallback(
			function () {
				if (!responseText.trim()) return
				setSubmitting(true)
				respondWorkflow(action.workflow_id, responseText.trim())
					.then(function () {
						setResponseText("")
						setShowResponse(false)
						onRefresh()
					})
					.catch(function (err) {
						console.error("Failed to respond:", err)
					})
					.finally(function () {
						setSubmitting(false)
					})
			},
			[action.workflow_id, responseText, onRefresh],
		)

		var handleDismiss = useCallback(
			function () {
				setSubmitting(true)
				dismissWorkflow(action.workflow_id)
					.then(function () {
						onRefresh()
					})
					.catch(function (err) {
						console.error("Failed to dismiss:", err)
					})
					.finally(function () {
						setSubmitting(false)
					})
			},
			[action.workflow_id, onRefresh],
		)

		var contextObj = null
		if (action.context) {
			try {
				contextObj = JSON.parse(action.context)
			} catch (e) {
				/* raw string */
			}
		}

		return React.createElement(
			Card,
			{ className: "mb-3" },
			React.createElement(
				CardHeader,
				{ className: "pb-2" },
				React.createElement(
					"div",
					{ className: "flex items-start justify-between gap-2" },
					React.createElement(
						"div",
						{ className: "min-w-0 flex-1" },
						React.createElement(
							CardTitle,
							{ className: "text-sm font-mono break-all" },
							action.workflow_id,
						),
						action.cron_job_id
							? React.createElement(
									"div",
									{ className: "text-xs text-text-tertiary mt-0.5" },
									"Cron job: ",
									React.createElement("code", null, action.cron_job_id),
								)
							: null,
					),
					React.createElement(Badge, null, "pending"),
				),
				React.createElement(
					"p",
					{ className: "text-sm text-text-secondary mt-2" },
					action.question,
				),
			),
			React.createElement(
				CardContent,
				{ className: "pt-0" },
				contextObj
					? React.createElement(
							"pre",
							{
								className:
									"text-xs bg-bg-tertiary rounded p-2 mb-3 overflow-x-auto max-h-32",
							},
							JSON.stringify(contextObj, null, 2),
						)
					: action.context
						? React.createElement(
								"p",
								{ className: "text-xs text-text-tertiary mb-3" },
								truncate(action.context, 200),
							)
						: null,
				React.createElement(
					"div",
					{ className: "text-xs text-text-tertiary mb-3" },
					"Created: " + fmtTime(action.created_at),
				),

				showResponse
					? React.createElement(
							"div",
							{ className: "space-y-2" },
							React.createElement(Label, null, "Your response:"),
							React.createElement(Input, {
								value: responseText,
								onChange: function (e) {
									setResponseText(e.target.value)
								},
								placeholder:
									"Type your answer, decision, or instructions\u2026",
							}),
							React.createElement(
								"div",
								{ className: "flex gap-2" },
								React.createElement(
									Button,
									{
										disabled: submitting || !responseText.trim(),
										onClick: handleRespond,
										size: "sm",
									},
									submitting ? "Sending\u2026" : "Submit Response",
								),
								React.createElement(
									Button,
									{
										ghost: true,
										size: "sm",
										onClick: function () {
											setShowResponse(false)
											setResponseText("")
										},
									},
									"Cancel",
								),
							),
						)
					: React.createElement(
							"div",
							{ className: "flex gap-2" },
							React.createElement(
								Button,
								{
									size: "sm",
									onClick: function () {
										setShowResponse(true)
									},
								},
								"Respond",
							),
							React.createElement(
								Button,
								{
									ghost: true,
									size: "sm",
									onClick: handleDismiss,
									disabled: submitting,
								},
								"Dismiss",
							),
						),
			),
		)
	}

	/** A single resolved workflow card. */
	function ResolvedCard(props) {
		var action = props.action
		return React.createElement(
			Card,
			{ className: "mb-3 opacity-70" },
			React.createElement(
				CardHeader,
				{ className: "pb-2" },
				React.createElement(
					"div",
					{ className: "flex items-start justify-between gap-2" },
					React.createElement(
						CardTitle,
						{ className: "text-sm font-mono break-all" },
						action.workflow_id,
					),
					React.createElement(Badge, null, "resolved"),
				),
				React.createElement(
					"p",
					{ className: "text-sm text-text-secondary mt-2" },
					action.question,
				),
			),
			React.createElement(
				CardContent,
				{ className: "pt-0" },
				React.createElement(
					"div",
					{ className: "bg-bg-tertiary rounded p-2 mb-2" },
					React.createElement(
						"span",
						{ className: "text-xs font-semibold text-text-secondary" },
						"Response: ",
					),
					React.createElement(
						"span",
						{ className: "text-sm" },
						action.response || "\u2014",
					),
				),
				React.createElement(
					"div",
					{ className: "text-xs text-text-tertiary" },
					"Created: " + fmtTime(action.created_at),
					" \u00b7 Resolved: " + fmtTime(action.responded_at),
				),
			),
		)
	}

	/** State keys list. */
	function StatePanel(props) {
		var keys = props.keys || []
		var onRefresh = props.onRefresh

		var _useState4 = useState(null)
		var expandedKey = _useState4[0]
		var setExpandedKey = _useState4[1]

		var _useState5 = useState(null)
		var keyValue = _useState5[0]
		var setKeyValue = _useState5[1]

		var handleClick = useCallback(
			function (key) {
				if (expandedKey === key) {
					setExpandedKey(null)
					setKeyValue(null)
					return
				}
				setExpandedKey(key)
				fetchJSON(BASE + "/state/" + encodeURIComponent(key))
					.then(function (data) {
						setKeyValue(data.value)
					})
					.catch(function () {
						setKeyValue("(failed to load)")
					})
			},
			[expandedKey],
		)

		var handleDelete = useCallback(
			function (key) {
				deleteStateKey(key)
					.then(function () {
						setExpandedKey(null)
						setKeyValue(null)
						onRefresh()
					})
					.catch(function (err) {
						console.error(err)
					})
			},
			[onRefresh],
		)

		if (keys.length === 0) {
			return React.createElement(
				"p",
				{ className: "text-sm text-text-tertiary py-4 text-center" },
				"No state keys stored yet. Agents will create state when they run with workflow_save_state().",
			)
		}

		return React.createElement(
			"div",
			{ className: "space-y-1" },
			keys.map(function (key) {
				var isExpanded = expandedKey === key
				return React.createElement(
					"div",
					{ key: key, className: "border border-border rounded" },
					React.createElement(
						"div",
						{
							className:
								"flex items-center justify-between p-2 cursor-pointer hover:bg-bg-tertiary",
							onClick: function () {
								handleClick(key)
							},
						},
						React.createElement("code", { className: "text-xs" }, key),
						React.createElement(
							Button,
							{
								ghost: true,
								size: "sm",
								onClick: function (e) {
									e.stopPropagation()
									handleDelete(key)
								},
							},
							"Delete",
						),
					),
					isExpanded
						? React.createElement(
								"pre",
								{
									className:
										"text-xs bg-bg-tertiary p-2 m-2 rounded overflow-x-auto max-h-64",
								},
								keyValue === null
									? "Loading\u2026"
									: typeof keyValue === "string"
										? keyValue
										: JSON.stringify(keyValue, null, 2),
							)
						: null,
				)
			}),
		)
	}

	// -----------------------------------------------------------------------
	// Main page component
	// -----------------------------------------------------------------------

	function WorkflowPage() {
		var _useState6 = useState("pending")
		var tab = _useState6[0]
		var setTab = _useState6[1]

		var _useState7 = useState(null)
		var pendingData = _useState7[0]
		var setPendingData = _useState7[1]

		var _useState8 = useState(null)
		var stateData = _useState8[0]
		var setStateData = _useState8[1]

		var _useState9 = useState(null)
		var statsData = _useState9[0]
		var setStatsData = _useState9[1]

		var _useState10 = useState(true)
		var loading = _useState10[0]
		var setLoading = _useState10[1]

		var loadAll = useCallback(function () {
			setLoading(true)
			Promise.all([fetchPending(), fetchState(), fetchStats()])
				.then(function (results) {
					setPendingData(results[0])
					setStateData(results[1])
					setStatsData(results[2])
				})
				.catch(function (err) {
					console.error("Workflow Engine: failed to load data", err)
				})
				.finally(function () {
					setLoading(false)
				})
		}, [])

		useEffect(
			function () {
				loadAll()
			},
			[loadAll],
		)

		var pending = (pendingData && pendingData.pending) || []
		var resolved = (pendingData && pendingData.resolved) || []
		var keys = (stateData && stateData.keys) || []

		if (loading) {
			return React.createElement(
				"div",
				{ className: "flex items-center justify-center py-12" },
				React.createElement(
					"div",
					{
						className:
							"inline-block h-6 w-6 animate-spin rounded-full border-2 border-muted border-t-primary",
						role: "status",
					},
					React.createElement(
						"span",
						{
							className: "sr-only",
						},
						"Loading\u2026",
					),
				),
			)
		}

		return React.createElement(
			"div",
			{ className: "max-w-3xl mx-auto" },
			React.createElement(
				"div",
				{ className: "flex items-center justify-between mb-4" },
				React.createElement(
					"h1",
					{ className: "text-lg font-semibold tracking-tight" },
					"Workflow Engine",
				),
				React.createElement(
					Button,
					{ size: "sm", ghost: true, onClick: loadAll },
					"Refresh",
				),
			),
			React.createElement(StatsBar, { stats: statsData }),

			React.createElement(
				Tabs,
				{ value: tab, onValueChange: setTab },
				React.createElement(
					TabsList,
					null,
					React.createElement(
						TabsTrigger,
						{ value: "pending" },
						"Pending (" + pending.length + ")",
					),
					React.createElement(
						TabsTrigger,
						{ value: "resolved" },
						"Resolved (" + resolved.length + ")",
					),
					React.createElement(
						TabsTrigger,
						{ value: "state" },
						"State (" + keys.length + ")",
					),
				),
				React.createElement(Separator, { className: "my-3" }),

				// Pending tab
				tab === "pending" &&
					React.createElement(
						"div",
						null,
						pending.length === 0
							? React.createElement(
									"p",
									{ className: "text-sm text-text-tertiary py-8 text-center" },
									"No pending workflows. Agents will create pending actions when they call workflow_wait_for_user().",
								)
							: pending.map(function (action) {
									return React.createElement(PendingCard, {
										key: action.workflow_id,
										action: action,
										onRefresh: loadAll,
									})
								}),
					),

				// Resolved tab
				tab === "resolved" &&
					React.createElement(
						"div",
						null,
						resolved.length === 0
							? React.createElement(
									"p",
									{ className: "text-sm text-text-tertiary py-8 text-center" },
									"No resolved workflows yet.",
								)
							: resolved.map(function (action) {
									return React.createElement(ResolvedCard, {
										key: action.workflow_id,
										action: action,
									})
								}),
					),

				// State tab
				tab === "state" &&
					React.createElement(StatePanel, { keys: keys, onRefresh: loadAll }),
			),
		)
	}

	// -----------------------------------------------------------------------
	// Register with the Hermes dashboard
	// -----------------------------------------------------------------------

	window.__HERMES_PLUGINS__.register("workflow", WorkflowPage)

	// Also inject a small summary into the Cron page bottom slot.
	window.__HERMES_PLUGINS__.registerSlot(
		"workflow",
		"cron:bottom",
		function CronPendingBadge() {
			var _useState11 = useState(null)
			var stats = _useState11[0]
			var setStats = _useState11[1]

			useEffect(function () {
				fetchStats()
					.then(function (data) {
						setStats(data)
					})
					.catch(function () {})
			}, [])

			if (!stats || stats.pending_workflows === 0) return null

			return React.createElement(
				"div",
				{
					className:
						"mt-3 rounded border border-amber-500/30 bg-amber-500/10 p-3",
				},
				React.createElement(
					"p",
					{ className: "text-xs text-amber-400 font-semibold" },
					"\u23F3 " +
						stats.pending_workflows +
						" workflow(s) awaiting your response. ",
					React.createElement(
						"a",
						{ href: "#/workflows", className: "underline" },
						"Review \u2192",
					),
				),
			)
		},
	)
})()
