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

	// ── API helpers ─────────────────────────────────────────────────────

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

	// ── Badge & Button helpers ──────────────────────────────────────────

	var badgeClass =
		"inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold"

	function Badge(props) {
		var extra = props.className || ""
		return React.createElement(
			"span",
			{
				className: badgeClass + " " + extra,
				style: props.style || {},
			},
			props.children,
		)
	}

	function Btn(props) {
		var disabled = props.disabled
		var cls =
			"inline-flex items-center rounded px-3 py-1.5 text-xs font-medium transition-colors"
		if (props.variant === "primary") {
			cls += " bg-primary text-primary-foreground hover:opacity-90"
		} else if (props.variant === "danger") {
			cls += " bg-red-600 text-white hover:bg-red-700"
		} else if (props.variant === "ghost") {
			cls += " text-text-secondary hover:bg-bg-tertiary"
		} else {
			cls += " border border-border hover:bg-bg-tertiary"
		}
		if (disabled) cls += " opacity-50 cursor-not-allowed"
		return React.createElement(
			"button",
			{
				onClick: props.onClick,
				disabled: disabled,
				className: cls,
				title: props.title || "",
			},
			props.children,
		)
	}

	function Field(props) {
		return React.createElement(
			"div",
			null,
			props.label
				? React.createElement(
						"label",
						{ className: "block text-sm font-medium mb-1" },
						props.label,
						props.required
							? React.createElement("span", { className: "text-red-400" }, " *")
							: null,
					)
				: null,
			props.isTextarea
				? React.createElement("textarea", {
						rows: props.rows || 4,
						className:
							"w-full rounded border border-border bg-bg-secondary px-3 py-2 text-sm font-mono",
						value: props.value,
						onChange: function (e) {
							props.onChange(e.target.value)
						},
						placeholder: props.placeholder || "",
					})
				: React.createElement("input", {
						type: props.type || "text",
						className:
							"w-full rounded border border-border bg-bg-secondary px-3 py-2 text-sm",
						value: props.value,
						onChange: function (e) {
							props.onChange(e.target.value)
						},
						placeholder: props.placeholder || "",
						checked: props.type === "checkbox" ? props.value : undefined,
					}),
		)
	}

	// ── Stats bar ───────────────────────────────────────────────────────

	function StatsBar(props) {
		var s = props.data
		if (!s) return null
		return React.createElement(
			"div",
			{ className: "flex flex-wrap gap-3 mb-4" },
			React.createElement(Badge, null, (s.count || 0) + " workflows"),
			s.pendingCount > 0
				? React.createElement(
						Badge,
						{ className: "text-amber-400 border-amber-500/30" },
						s.pendingCount + " awaiting input",
					)
				: null,
		)
	}

	// ── Create/Edit Modal ───────────────────────────────────────────────

	function WorkflowFormModal(props) {
		var onClose = props.onClose
		var onRefresh = props.onRefresh
		var editWf = props.editWorkflow

		var _id = useState(editWf ? editWf.id : ""),
			wfId = _id[0],
			setWfId = _id[1]
		var _name = useState(editWf ? editWf.name : ""),
			name = _name[0],
			setName = _name[1]
		var _desc = useState(editWf ? editWf.description || "" : ""),
			description = _desc[0],
			setDesc = _desc[1]
		var _cron = useState(editWf ? editWf.cron_expression : ""),
			cron = _cron[0],
			setCron = _cron[1]
		var _prompt = useState(editWf ? editWf.prompt || "" : ""),
			prompt = _prompt[0],
			setPrompt = _prompt[1]
		var _enabled = useState(editWf ? editWf.enabled : true),
			enabled = _enabled[0],
			setEnabled = _enabled[1]
		var _saving = useState(false),
			saving = _saving[0],
			setSaving = _saving[1]
		var _error = useState(""),
			error = _error[0],
			setError = _error[1]

		var isEdit = !!editWf
		var titleText = isEdit ? "Edit Workflow" : "Create Workflow"

		var submit = useCallback(
			function () {
				setError("")
				if (!wfId.trim()) {
					setError("Workflow ID is required.")
					return
				}
				if (!name.trim()) {
					setError("Name is required.")
					return
				}
				if (!cron.trim()) {
					setError("Cron expression is required.")
					return
				}
				if (cron.trim().split(/\s+/).length !== 5) {
					setError("Cron must have exactly 5 fields (e.g. '0 9 * * 1-5').")
					return
				}
				if (!prompt.trim()) {
					setError("Prompt is required.")
					return
				}

				setSaving(true)
				var url = isEdit
					? "/workflows/" + encodeURIComponent(wfId)
					: "/workflows"
				var method = isEdit ? "PUT" : "POST"
				var body = isEdit
					? {
							name: name,
							description: description,
							cron_expression: cron,
							prompt: prompt,
							enabled: enabled,
						}
					: {
							id: wfId,
							name: name,
							description: description,
							cron_expression: cron,
							prompt: prompt,
						}

				api(url, {
					method: method,
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify(body),
				})
					.then(function () {
						onClose()
						onRefresh()
					})
					.catch(function (e) {
						setError("Failed: " + e.message)
					})
					.finally(function () {
						setSaving(false)
					})
			},
			[
				wfId,
				name,
				description,
				cron,
				prompt,
				enabled,
				isEdit,
				onClose,
				onRefresh,
			],
		)

		return React.createElement(
			"div",
			{
				className: "fixed inset-0 z-50 flex items-center justify-center",
				style: { background: "rgba(0,0,0,0.55)" },
			},
			React.createElement(
				"div",
				{
					className:
						"bg-bg-primary rounded-lg border border-border shadow-xl w-full max-w-xl mx-4 p-6 max-h-[90vh] overflow-y-auto",
				},
				// Header
				React.createElement(
					"div",
					{ className: "flex items-start justify-between mb-4" },
					React.createElement(
						"h2",
						{ className: "text-lg font-semibold" },
						titleText,
					),
					React.createElement(
						"button",
						{
							onClick: onClose,
							className:
								"inline-flex items-center rounded px-2 py-1 text-text-tertiary hover:text-text-secondary hover:bg-bg-tertiary",
						},
						"\u2715",
					),
				),
				// Error
				error
					? React.createElement(
							"div",
							{
								className:
									"mb-3 rounded border border-red-500/30 bg-red-500/10 p-2 text-xs text-red-400",
							},
							error,
						)
					: null,
				// Form fields
				React.createElement(
					"div",
					{ className: "space-y-3" },
					isEdit
						? null
						: React.createElement(Field, {
								label: "Workflow ID",
								required: true,
								value: wfId,
								onChange: setWfId,
								placeholder: "e.g. daily_code_review",
							}),
					React.createElement(Field, {
						label: "Name",
						required: true,
						value: name,
						onChange: setName,
						placeholder: "e.g. Daily Code Review Rotation",
					}),
					React.createElement(Field, {
						label: "Description",
						value: description,
						onChange: setDesc,
						placeholder: "What does this workflow do?",
					}),
					React.createElement(Field, {
						label: "Cron Expression",
						required: true,
						value: cron,
						onChange: setCron,
						placeholder: "e.g. 0 9 * * 1-5 (weekdays at 9am)",
					}),
					React.createElement(Field, {
						label: "System Prompt",
						required: true,
						isTextarea: true,
						rows: 6,
						value: prompt,
						onChange: setPrompt,
						placeholder: "Instructions for the agent on each run...",
					}),
					isEdit
						? React.createElement(
								"div",
								{ className: "flex items-center gap-2" },
								React.createElement("input", {
									type: "checkbox",
									id: "wf-enabled",
									checked: enabled,
									onChange: function (e) {
										setEnabled(e.target.checked)
									},
									className: "rounded",
								}),
								React.createElement(
									"label",
									{ htmlFor: "wf-enabled", className: "text-sm" },
									"Enabled",
								),
							)
						: null,
				),
				// Actions
				React.createElement(
					"div",
					{ className: "flex gap-2 justify-end mt-4" },
					React.createElement(Btn, { onClick: onClose }, "Cancel"),
					React.createElement(
						Btn,
						{ variant: "primary", onClick: submit, disabled: saving },
						saving ? "Saving\u2026" : isEdit ? "Update" : "Create",
					),
				),
			),
		)
	}

	// ── Workflow Card ────────────────────────────────────────────────────

	function WorkflowCard(props) {
		var wf = props.wf
		var onRefresh = props.onRefresh
		var onEdit = props.onEdit
		var onSelect = props.onSelect

		var _toggling = useState(false),
			toggling = _toggling[0],
			setToggling = _toggling[1]

		var toggleEnabled = useCallback(
			function (e) {
				e.stopPropagation()
				setToggling(true)
				api("/workflows/" + encodeURIComponent(wf.id), {
					method: "PUT",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify({ enabled: !wf.enabled }),
				})
					.then(function () {
						onRefresh()
					})
					.catch(function (err) {
						console.error(err)
					})
					.finally(function () {
						setToggling(false)
					})
			},
			[wf.id, wf.enabled, onRefresh],
		)

		var del = useCallback(
			function (e) {
				e.stopPropagation()
				if (
					!confirm(
						"Delete workflow '" +
							wf.name +
							"' and all its state? This cannot be undone.",
					)
				)
					return
				api("/workflows/" + encodeURIComponent(wf.id), { method: "DELETE" })
					.then(function () {
						onRefresh()
					})
					.catch(function (err) {
						console.error(err)
					})
			},
			[wf.id, wf.name, onRefresh],
		)

		var runNow = useCallback(
			function (e) {
				e.stopPropagation()
				api("/workflows/" + encodeURIComponent(wf.id) + "/run", {
					method: "POST",
				})
					.then(function (res) {
						if (res.success) {
							alert(
								"Run triggered. Status: " +
									((res.run && res.run.status) || "unknown"),
							)
						} else {
							alert("Could not run: " + (res.message || res.error || "unknown"))
						}
						onRefresh()
					})
					.catch(function (err) {
						console.error(err)
					})
			},
			[wf.id, onRefresh],
		)

		var statusColor = wf.enabled ? "#22c55e" : "#6b7280"

		return React.createElement(
			"div",
			{
				className:
					"rounded-lg border border-border bg-bg-secondary p-4 cursor-pointer hover:border-primary/40 transition-colors",
				onClick: function () {
					onSelect(wf.id)
				},
			},
			// Header row
			React.createElement(
				"div",
				{ className: "flex items-start justify-between mb-2" },
				React.createElement(
					"div",
					{ className: "flex items-center gap-2" },
					React.createElement("span", {
						className: "inline-block w-2 h-2 rounded-full flex-shrink-0",
						style: { backgroundColor: statusColor, marginTop: "0.35rem" },
					}),
					React.createElement(
						"div",
						null,
						React.createElement(
							"h3",
							{ className: "text-sm font-semibold" },
							wf.name,
						),
						React.createElement(
							"span",
							{ className: "text-xs text-text-tertiary font-mono" },
							wf.id,
						),
					),
				),
				React.createElement(
					"div",
					{
						className: "flex gap-1",
						onClick: function (e) {
							e.stopPropagation()
						},
					},
					React.createElement(
						Btn,
						{ variant: "ghost", onClick: runNow, title: "Run now" },
						"\u25B6",
					),
					React.createElement(
						Btn,
						{
							variant: "ghost",
							onClick: function () {
								onEdit(wf)
							},
							title: "Edit",
						},
						"\u270E",
					),
					React.createElement(
						Btn,
						{ variant: "ghost", onClick: del, title: "Delete" },
						"\u2715",
					),
				),
			),
			// Meta row
			React.createElement(
				"div",
				{
					className:
						"flex flex-wrap gap-x-4 gap-y-1 text-xs text-text-tertiary mt-1",
				},
				React.createElement(
					"span",
					null,
					"Schedule: ",
					React.createElement(
						"code",
						{ className: "text-text-secondary" },
						wf.cron_expression,
					),
				),
				wf.next_run
					? React.createElement(
							"span",
							null,
							"Next: ",
							fmtTime(new Date(wf.next_run).getTime() / 1000),
						)
					: null,
				React.createElement("span", null, wf.enabled ? "Active" : "Paused"),
			),
			// Description
			wf.description
				? React.createElement(
						"p",
						{ className: "text-xs text-text-tertiary mt-2 line-clamp-2" },
						wf.description,
					)
				: null,
		)
	}

	// ── Pending Action Row ───────────────────────────────────────────────

	function PendingRow(props) {
		var action = props.action
		var onRefresh = props.onRefresh

		var _resp = useState(""),
			response = _resp[0],
			setResponse = _resp[1]
		var _submitting = useState(false),
			submitting = _submitting[0],
			setSubmitting = _submitting[1]

		var respond = useCallback(
			function () {
				if (!response.trim()) return
				setSubmitting(true)
				api("/pending/" + encodeURIComponent(action.id) + "/respond", {
					method: "POST",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify({ response: response.trim() }),
				})
					.then(function () {
						setResponse("")
						onRefresh()
					})
					.catch(function (e) {
						console.error(e)
					})
					.finally(function () {
						setSubmitting(false)
					})
			},
			[action.id, response, onRefresh],
		)

		var dismiss = useCallback(
			function () {
				api("/pending/" + encodeURIComponent(action.id) + "/dismiss", {
					method: "POST",
				})
					.then(function () {
						onRefresh()
					})
					.catch(function (e) {
						console.error(e)
					})
			},
			[action.id, onRefresh],
		)

		return React.createElement(
			"div",
			{
				className: "rounded border border-amber-500/30 bg-amber-500/5 p-3 mb-2",
			},
			React.createElement(
				"div",
				{ className: "flex items-start justify-between mb-2" },
				React.createElement(
					"div",
					{ className: "flex-1 min-w-0" },
					React.createElement(
						"p",
						{ className: "text-sm font-medium" },
						action.question,
					),
					action.context
						? React.createElement(
								"pre",
								{
									className:
										"mt-1 text-xs text-text-tertiary whitespace-pre-wrap break-words max-h-24 overflow-y-auto bg-bg-tertiary rounded p-2",
								},
								truncate(action.context, 500),
							)
						: null,
					React.createElement(
						"span",
						{ className: "text-xs text-text-tertiary mt-1 block" },
						"From: ",
						React.createElement("code", null, action.workflow_id),
						" \u2022 ",
						fmtTime(action.created_at),
					),
				),
			),
			React.createElement(
				"div",
				{ className: "flex gap-2 items-end" },
				React.createElement("input", {
					className:
						"flex-1 rounded border border-border bg-bg-secondary px-3 py-1.5 text-sm",
					placeholder: "Type your response...",
					value: response,
					onChange: function (e) {
						setResponse(e.target.value)
					},
					onKeyDown: function (e) {
						if (e.key === "Enter" && response.trim()) respond()
					},
				}),
				React.createElement(
					Btn,
					{
						variant: "primary",
						onClick: respond,
						disabled: submitting || !response.trim(),
					},
					submitting ? "Sending\u2026" : "Respond",
				),
				React.createElement(
					Btn,
					{ variant: "ghost", onClick: dismiss },
					"Dismiss",
				),
			),
		)
	}

	// ── Workflow Detail Panel ────────────────────────────────────────────

	function WorkflowDetail(props) {
		var workflowId = props.workflowId
		var onBack = props.onBack
		var onRefresh = props.onRefresh

		var _data = useState(null),
			data = _data[0],
			setData = _data[1]
		var _loading = useState(true),
			loading = _loading[0],
			setLoading = _loading[1]
		var _error = useState(""),
			error = _error[0],
			setError = _error[1]

		var load = useCallback(
			function () {
				setLoading(true)
				setError("")
				api("/workflows/" + encodeURIComponent(workflowId))
					.then(function (d) {
						if (d.error) {
							setError(d.error)
							setData(null)
						} else {
							setData(d)
						}
					})
					.catch(function (e) {
						setError(e.message)
					})
					.finally(function () {
						setLoading(false)
					})
			},
			[workflowId],
		)

		useEffect(
			function () {
				load()
			},
			[load],
		)

		if (loading) {
			return React.createElement(
				"div",
				{ className: "p-8 text-center text-text-tertiary" },
				"Loading\u2026",
			)
		}
		if (error) {
			return React.createElement(
				"div",
				{ className: "p-4" },
				React.createElement("div", { className: "text-red-400 mb-2" }, error),
				React.createElement(Btn, { onClick: onBack }, "\u2190 Back"),
			)
		}
		if (!data || !data.found) {
			return React.createElement(
				"div",
				{ className: "p-4" },
				React.createElement(
					"p",
					{ className: "text-text-tertiary mb-2" },
					"Workflow not found.",
				),
				React.createElement(Btn, { onClick: onBack }, "\u2190 Back"),
			)
		}

		var wf = data.workflow
		var stateKeys = data.state_keys || []
		var stateData = data.state || {}
		var pendingActions = data.pending_actions || []
		var resolvedActions = data.resolved_actions || []

		return React.createElement(
			"div",
			{ className: "p-4" },
			// Back + header
			React.createElement(
				"div",
				{ className: "flex items-center gap-3 mb-4" },
				React.createElement(Btn, { onClick: onBack }, "\u2190 Back"),
				React.createElement(
					"div",
					null,
					React.createElement(
						"h2",
						{ className: "text-lg font-semibold" },
						wf.name,
					),
					React.createElement(
						"span",
						{ className: "text-xs text-text-tertiary font-mono" },
						wf.id,
					),
				),
			),

			// Meta card
			React.createElement(
				"div",
				{
					className: "rounded-lg border border-border bg-bg-secondary p-4 mb-4",
				},
				React.createElement(
					"div",
					{ className: "grid grid-cols-2 gap-3 text-sm" },
					React.createElement(
						"div",
						null,
						React.createElement(
							"span",
							{ className: "text-text-tertiary" },
							"Schedule",
						),
						React.createElement(
							"p",
							{ className: "font-mono" },
							wf.cron_expression,
						),
					),
					React.createElement(
						"div",
						null,
						React.createElement(
							"span",
							{ className: "text-text-tertiary" },
							"Next Run",
						),
						React.createElement(
							"p",
							null,
							wf.next_run
								? fmtTime(new Date(wf.next_run).getTime() / 1000)
								: "\u2014",
						),
					),
					React.createElement(
						"div",
						null,
						React.createElement(
							"span",
							{ className: "text-text-tertiary" },
							"Status",
						),
						React.createElement(
							"p",
							null,
							React.createElement("span", {
								className: "inline-block w-2 h-2 rounded-full mr-1",
								style: { backgroundColor: wf.enabled ? "#22c55e" : "#6b7280" },
							}),
							wf.enabled ? "Enabled" : "Disabled",
						),
					),
					React.createElement(
						"div",
						null,
						React.createElement(
							"span",
							{ className: "text-text-tertiary" },
							"State Keys",
						),
						React.createElement("p", null, stateKeys.length),
					),
				),
				wf.description
					? React.createElement(
							"p",
							{
								className:
									"text-sm text-text-tertiary mt-3 border-t border-border pt-3",
							},
							wf.description,
						)
					: null,
			),

			// Prompt
			React.createElement(
				"div",
				{ className: "mb-4" },
				React.createElement(
					"h3",
					{ className: "text-sm font-semibold mb-2" },
					"System Prompt",
				),
				React.createElement(
					"pre",
					{
						className:
							"text-xs bg-bg-tertiary rounded p-3 max-h-48 overflow-y-auto whitespace-pre-wrap break-words",
					},
					wf.prompt,
				),
			),

			// State keys
			React.createElement(
				"div",
				{ className: "mb-4" },
				React.createElement(
					"h3",
					{ className: "text-sm font-semibold mb-2" },
					"State (",
					stateKeys.length,
					" keys)",
				),
				stateKeys.length > 0
					? React.createElement(
							"div",
							{ className: "space-y-1" },
							stateKeys.map(function (key) {
								var val = stateData[key]
								var preview =
									typeof val === "string" ? val : JSON.stringify(val)
								return React.createElement(
									"details",
									{
										key: key,
										className: "rounded border border-border bg-bg-secondary",
									},
									React.createElement(
										"summary",
										{
											className:
												"px-3 py-2 text-sm font-mono cursor-pointer hover:bg-bg-tertiary",
										},
										key,
									),
									React.createElement(
										"pre",
										{
											className:
												"px-3 py-2 text-xs text-text-tertiary border-t border-border whitespace-pre-wrap break-words max-h-32 overflow-y-auto",
										},
										truncate(preview, 1000),
									),
								)
							}),
						)
					: React.createElement(
							"p",
							{ className: "text-xs text-text-tertiary" },
							"No state saved yet.",
						),
			),

			// Pending actions
			React.createElement(
				"div",
				{ className: "mb-4" },
				React.createElement(
					"h3",
					{ className: "text-sm font-semibold mb-2" },
					"Pending Input (",
					pendingActions.length,
					")",
				),
				pendingActions.length > 0
					? pendingActions.map(function (a) {
							return React.createElement(PendingRow, {
								key: a.id,
								action: a,
								onRefresh: load,
							})
						})
					: React.createElement(
							"p",
							{ className: "text-xs text-text-tertiary" },
							"No pending actions.",
						),
			),

			// Resolved actions
			resolvedActions.length > 0
				? React.createElement(
						"div",
						{ className: "mb-4" },
						React.createElement(
							"h3",
							{ className: "text-sm font-semibold mb-2" },
							"Recently Resolved (",
							resolvedActions.length,
							")",
						),
						React.createElement(
							"div",
							{ className: "space-y-1" },
							resolvedActions.slice(0, 5).map(function (a) {
								return React.createElement(
									"div",
									{
										key: a.id,
										className:
											"rounded border border-border bg-bg-secondary p-2 text-xs",
									},
									React.createElement(
										"p",
										{ className: "text-text-secondary" },
										"Q: ",
										a.question,
									),
									React.createElement(
										"p",
										{ className: "text-green-400 mt-1" },
										"A: ",
										a.response,
									),
								)
							}),
						),
					)
				: null,
		)
	}

	// ── Main App ─────────────────────────────────────────────────────────

	function App() {
		var _workflows = useState([]),
			workflows = _workflows[0],
			setWorkflows = _workflows[1]
		var _loading = useState(true),
			loading = _loading[0],
			setLoading = _loading[1]
		var _error = useState(""),
			error = _error[0],
			setError = _error[1]
		var _showForm = useState(false),
			showForm = _showForm[0],
			setShowForm = _showForm[1]
		var _editWf = useState(null),
			editWf = _editWf[0],
			setEditWf = _editWf[1]
		var _selectedWf = useState(null),
			selectedWf = _selectedWf[0],
			setSelectedWf = _selectedWf[1]
		var _pending = useState([]),
			pending = _pending[0],
			setPending = _pending[1]

		var load = useCallback(function () {
			setLoading(true)
			setError("")
			Promise.all([api("/workflows"), api("/pending")])
				.then(function (results) {
					setWorkflows(results[0].workflows || [])
					setPending(results[1].pending || [])
				})
				.catch(function (e) {
					setError("Failed to load: " + e.message)
				})
				.finally(function () {
					setLoading(false)
				})
		}, [])

		useEffect(
			function () {
				load()
			},
			[load],
		)

		var handleEdit = useCallback(function (wf) {
			setEditWf(wf)
			setShowForm(true)
		}, [])

		var handleCloseForm = useCallback(function () {
			setShowForm(false)
			setEditWf(null)
		}, [])

		// ── Selected workflow detail view ─────────────────────────────
		if (selectedWf) {
			return React.createElement(WorkflowDetail, {
				workflowId: selectedWf,
				onBack: function () {
					setSelectedWf(null)
					load()
				},
				onRefresh: load,
			})
		}

		return React.createElement(
			"div",
			{ className: "p-4" },
			// Header row
			React.createElement(
				"div",
				{ className: "flex items-center justify-between mb-4" },
				React.createElement(
					"h2",
					{ className: "text-lg font-semibold" },
					"Workflows",
				),
				React.createElement(
					Btn,
					{
						variant: "primary",
						onClick: function () {
							setEditWf(null)
							setShowForm(true)
						},
					},
					"+ New Workflow",
				),
			),

			// Error
			error
				? React.createElement(
						"div",
						{
							className:
								"mb-4 rounded border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-400",
						},
						error,
					)
				: null,

			// Global pending actions (if any)
			pending.length > 0
				? React.createElement(
						"div",
						{ className: "mb-4" },
						React.createElement(
							"h3",
							{
								className: "text-sm font-semibold mb-2 flex items-center gap-2",
							},
							"\u23F3 Pending Input (",
							pending.length,
							")",
						),
						pending.map(function (a) {
							return React.createElement(PendingRow, {
								key: a.id,
								action: a,
								onRefresh: load,
							})
						}),
					)
				: null,

			// Workflow cards
			loading
				? React.createElement(
						"div",
						{ className: "text-center text-text-tertiary py-8" },
						"Loading workflows\u2026",
					)
				: workflows.length === 0
					? React.createElement(
							"div",
							{ className: "text-center text-text-tertiary py-8" },
							"No workflows yet. Click '+ New Workflow' to create your first scheduled workflow.",
						)
					: React.createElement(
							"div",
							{ className: "grid gap-3" },
							workflows.map(function (wf) {
								return React.createElement(WorkflowCard, {
									key: wf.id,
									wf: wf,
									onRefresh: load,
									onEdit: handleEdit,
									onSelect: setSelectedWf,
								})
							}),
						),

			// Create/Edit modal
			showForm
				? React.createElement(WorkflowFormModal, {
						onClose: handleCloseForm,
						onRefresh: load,
						editWorkflow: editWf,
					})
				: null,
		)
	}

	// ── Mount ────────────────────────────────────────────────────────────

	SDK.render(App)
})()
