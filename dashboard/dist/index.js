;(function () {
	"use strict"

	var SDK = window.__HERMES_PLUGIN_SDK__
	if (!SDK || !window.__HERMES_PLUGINS__) return

	var React = SDK.React
	var hooks = SDK.hooks
	var useState = hooks.useState
	var useEffect = hooks.useEffect
	var useCallback = hooks.useCallback

	var components = SDK.components
	var Button = components.Button
	var Badge = components.Badge
	var Input = components.Input
	var Label = components.Label
	var Card = components.Card
	var CardHeader = components.CardHeader
	var CardTitle = components.CardTitle
	var CardContent = components.CardContent

	var fetchJSON = SDK.fetchJSON
	var cn = SDK.utils.cn
	var timeAgo = SDK.utils.timeAgo

	// ── Helpers ──────────────────────────────────────────────────────────

	function fmtTime(ts) {
		if (!ts) return "\u2014"
		return timeAgo ? timeAgo(ts) : new Date(ts * 1000).toLocaleString()
	}

	function truncate(str, max) {
		if (!str) return "\u2014"
		return str.length > max ? str.slice(0, max) + "\u2026" : str
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
					? "/api/plugins/workflow/workflows/" + encodeURIComponent(wfId)
					: "/api/plugins/workflow/workflows"
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

				fetchJSON(url, { method: method, body: JSON.stringify(body) })
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
					className: cn(
						"bg-bg-primary rounded-lg border border-border shadow-xl w-full max-w-xl mx-4 p-6 max-h-[90vh] overflow-y-auto",
					),
				},
				React.createElement(
					"div",
					{ className: "flex items-start justify-between mb-4" },
					React.createElement(
						"h2",
						{ className: "text-lg font-semibold" },
						titleText,
					),
					React.createElement(
						Button,
						{ variant: "ghost", size: "icon", onClick: onClose },
						"\u2715",
					),
				),
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
				React.createElement(
					"div",
					{ className: "space-y-3" },
					isEdit
						? null
						: React.createElement(
								"div",
								null,
								React.createElement(
									Label,
									null,
									"Workflow ID",
									React.createElement(
										"span",
										{ className: "text-red-400" },
										" *",
									),
								),
								React.createElement(Input, {
									value: wfId,
									onChange: function (e) {
										setWfId(e.target.value)
									},
									placeholder: "e.g. daily_code_review",
								}),
							),
					React.createElement(
						"div",
						null,
						React.createElement(
							Label,
							null,
							"Name",
							React.createElement("span", { className: "text-red-400" }, " *"),
						),
						React.createElement(Input, {
							value: name,
							onChange: function (e) {
								setName(e.target.value)
							},
							placeholder: "e.g. Daily Code Review Rotation",
						}),
					),
					React.createElement(
						"div",
						null,
						React.createElement(Label, null, "Description"),
						React.createElement(Input, {
							value: description,
							onChange: function (e) {
								setDesc(e.target.value)
							},
							placeholder: "What does this workflow do?",
						}),
					),
					React.createElement(
						"div",
						null,
						React.createElement(
							Label,
							null,
							"Cron Expression",
							React.createElement("span", { className: "text-red-400" }, " *"),
						),
						React.createElement(Input, {
							value: cron,
							onChange: function (e) {
								setCron(e.target.value)
							},
							placeholder: "e.g. 0 9 * * 1-5 (weekdays at 9am)",
						}),
					),
					React.createElement(
						"div",
						null,
						React.createElement(
							Label,
							null,
							"System Prompt",
							React.createElement("span", { className: "text-red-400" }, " *"),
						),
						React.createElement("textarea", {
							rows: 6,
							className:
								"w-full rounded border border-border bg-bg-secondary px-3 py-2 text-sm font-mono",
							value: prompt,
							onChange: function (e) {
								setPrompt(e.target.value)
							},
							placeholder: "Instructions for the agent on each run...",
						}),
					),
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
									Label,
									{ htmlFor: "wf-enabled" },
									"Enabled",
								),
							)
						: null,
				),
				React.createElement(
					"div",
					{ className: "flex gap-2 justify-end mt-4" },
					React.createElement(
						Button,
						{ variant: "outline", onClick: onClose },
						"Cancel",
					),
					React.createElement(
						Button,
						{ onClick: submit, disabled: saving },
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

		var toggleEnabled = useCallback(
			function (e) {
				e.stopPropagation()
				fetchJSON(
					"/api/plugins/workflow/workflows/" + encodeURIComponent(wf.id),
					{
						method: "PUT",
						body: JSON.stringify({ enabled: !wf.enabled }),
					},
				)
					.then(function () {
						onRefresh()
					})
					.catch(function (err) {
						console.error(err)
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
				fetchJSON(
					"/api/plugins/workflow/workflows/" + encodeURIComponent(wf.id),
					{ method: "DELETE" },
				)
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
				fetchJSON(
					"/api/plugins/workflow/workflows/" +
						encodeURIComponent(wf.id) +
						"/run",
					{ method: "POST" },
				)
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

		return React.createElement(
			"div",
			{
				className:
					"rounded-lg border border-border bg-bg-secondary p-4 cursor-pointer hover:border-primary/40 transition-colors",
				onClick: function () {
					onSelect(wf.id)
				},
			},
			React.createElement(
				"div",
				{ className: "flex items-start justify-between mb-2" },
				React.createElement(
					"div",
					{ className: "flex items-center gap-2" },
					React.createElement(
						Badge,
						{ variant: wf.enabled ? "default" : "secondary" },
						wf.enabled ? "\u25CF Active" : "\u25CB Paused",
					),
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
						Button,
						{
							variant: "ghost",
							size: "icon",
							onClick: runNow,
							title: "Run now",
						},
						"\u25B6",
					),
					React.createElement(
						Button,
						{
							variant: "ghost",
							size: "icon",
							onClick: function () {
								onEdit(wf)
							},
							title: "Edit",
						},
						"\u270E",
					),
					React.createElement(
						Button,
						{ variant: "ghost", size: "icon", onClick: del, title: "Delete" },
						"\u2715",
					),
				),
			),
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
			),
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
				fetchJSON(
					"/api/plugins/workflow/pending/" +
						encodeURIComponent(action.id) +
						"/respond",
					{
						method: "POST",
						body: JSON.stringify({ response: response.trim() }),
					},
				)
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
				fetchJSON(
					"/api/plugins/workflow/pending/" +
						encodeURIComponent(action.id) +
						"/dismiss",
					{ method: "POST" },
				)
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
				{ className: "mb-2" },
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
			React.createElement(
				"div",
				{ className: "flex gap-2 items-end" },
				React.createElement(Input, {
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
					Button,
					{ onClick: respond, disabled: submitting || !response.trim() },
					submitting ? "Sending\u2026" : "Respond",
				),
				React.createElement(
					Button,
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
				fetchJSON(
					"/api/plugins/workflow/workflows/" + encodeURIComponent(workflowId),
				)
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

		if (loading)
			return React.createElement(
				"div",
				{ className: "p-8 text-center text-text-tertiary" },
				"Loading\u2026",
			)

		if (error)
			return React.createElement(
				"div",
				{ className: "p-4" },
				React.createElement("div", { className: "text-red-400 mb-2" }, error),
				React.createElement(
					Button,
					{ variant: "outline", onClick: onBack },
					"\u2190 Back",
				),
			)

		if (!data || !data.found)
			return React.createElement(
				"div",
				{ className: "p-4" },
				React.createElement(
					"p",
					{ className: "text-text-tertiary mb-2" },
					"Workflow not found.",
				),
				React.createElement(
					Button,
					{ variant: "outline", onClick: onBack },
					"\u2190 Back",
				),
			)

		var wf = data.workflow
		var stateKeys = data.state_keys || []
		var stateData = data.state || {}
		var pendingActions = data.pending_actions || []
		var resolvedActions = data.resolved_actions || []

		return React.createElement(
			"div",
			{ className: "p-4" },
			React.createElement(
				"div",
				{ className: "flex items-center gap-3 mb-4" },
				React.createElement(
					Button,
					{ variant: "outline", onClick: onBack },
					"\u2190 Back",
				),
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

			// Meta Card
			React.createElement(
				Card,
				{ className: "mb-4" },
				React.createElement(
					CardContent,
					{ className: "pt-4" },
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
								React.createElement(
									Badge,
									{ variant: wf.enabled ? "default" : "secondary" },
									wf.enabled ? "Enabled" : "Disabled",
								),
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
			),

			// Prompt
			React.createElement(
				Card,
				{ className: "mb-4" },
				React.createElement(
					CardHeader,
					null,
					React.createElement(
						CardTitle,
						{ className: "text-sm" },
						"System Prompt",
					),
				),
				React.createElement(
					CardContent,
					null,
					React.createElement(
						"pre",
						{
							className:
								"text-xs bg-bg-tertiary rounded p-3 max-h-48 overflow-y-auto whitespace-pre-wrap break-words",
						},
						wf.prompt,
					),
				),
			),

			// State
			React.createElement(
				Card,
				{ className: "mb-4" },
				React.createElement(
					CardHeader,
					null,
					React.createElement(
						CardTitle,
						{ className: "text-sm" },
						"State (" + stateKeys.length + " keys)",
					),
				),
				React.createElement(
					CardContent,
					null,
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
			),

			// Pending actions
			pendingActions.length > 0
				? React.createElement(
						Card,
						{ className: "mb-4" },
						React.createElement(
							CardHeader,
							null,
							React.createElement(
								CardTitle,
								{ className: "text-sm" },
								"Pending Input (" + pendingActions.length + ")",
							),
						),
						React.createElement(
							CardContent,
							null,
							pendingActions.map(function (a) {
								return React.createElement(PendingRow, {
									key: a.id,
									action: a,
									onRefresh: load,
								})
							}),
						),
					)
				: null,

			// Resolved actions
			resolvedActions.length > 0
				? React.createElement(
						Card,
						{ className: "mb-4" },
						React.createElement(
							CardHeader,
							null,
							React.createElement(
								CardTitle,
								{ className: "text-sm" },
								"Recently Resolved (" + resolvedActions.length + ")",
							),
						),
						React.createElement(
							CardContent,
							null,
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
			Promise.all([
				fetchJSON("/api/plugins/workflow/workflows"),
				fetchJSON("/api/plugins/workflow/pending"),
			])
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
			React.createElement(
				"div",
				{ className: "flex items-center justify-between mb-4" },
				React.createElement(
					"h2",
					{ className: "text-lg font-semibold" },
					"Workflows",
				),
				React.createElement(
					Button,
					{
						onClick: function () {
							setEditWf(null)
							setShowForm(true)
						},
					},
					"+ New Workflow",
				),
			),

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

			pending.length > 0
				? React.createElement(
						Card,
						{ className: "mb-4" },
						React.createElement(
							CardHeader,
							null,
							React.createElement(
								CardTitle,
								{ className: "text-sm flex items-center gap-2" },
								"\u23F3 Pending Input (" + pending.length + ")",
							),
						),
						React.createElement(
							CardContent,
							null,
							pending.map(function (a) {
								return React.createElement(PendingRow, {
									key: a.id,
									action: a,
									onRefresh: load,
								})
							}),
						),
					)
				: null,

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

			showForm
				? React.createElement(WorkflowFormModal, {
						onClose: handleCloseForm,
						onRefresh: load,
						editWorkflow: editWf,
					})
				: null,
		)
	}

	// ── Register ──────────────────────────────────────────────────────────

	window.__HERMES_PLUGINS__.register("workflow", App)
})()
