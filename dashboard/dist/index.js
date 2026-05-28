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

	// ── Create / Edit Modal (cron-style backdrop blur) ──────────────────

	function WorkflowFormModal(props) {
		var onClose = props.onClose
		var onRefresh = props.onRefresh
		var onToast = props.onToast
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
		var _saving = useState(false),
			saving = _saving[0],
			setSaving = _saving[1]
		var _error = useState(""),
			error = _error[0],
			setError = _error[1]

		var isEdit = !!editWf
		var titleText = isEdit ? "Edit Workflow" : "New Workflow"

		var isEdit = !!editWf
		var titleText = isEdit ? "Edit Workflow" : "New Workflow"

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
				// Validate each cron field matches valid cron patterns
				var cronFields = cron.trim().split(/\s+/)
				var cronFieldRe = /^(\*|\d+(-\d+)?(,\d+(-\d+)?)*|\*\/\d+)$/
				for (var i = 0; i < cronFields.length; i++) {
					if (!cronFieldRe.test(cronFields[i])) {
						setError(
							"Invalid cron field '" +
								cronFields[i] +
								"'. Use numbers, '*', ranges like '1-5', lists like '1,3,5', or steps like '*/15'.",
						)
						return
					}
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
						onToast(
							(isEdit ? "Updated" : "Created") +
								': "' +
								truncate(name || wfId, 30) +
								'"',
							"success",
						)
						onRefresh(true)
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
				isEdit,
				onClose,
				onToast,
				onRefresh,
			],
		)

		return React.createElement(
			"div",
			{
				className:
					"fixed inset-0 z-[99999] flex items-center justify-center p-4",
				style: { background: "rgba(0,0,0,0.55)", backdropFilter: "blur(4px)" },
				onClick: function (e) {
					if (e.target === e.currentTarget) onClose()
				},
			},
			React.createElement(
				"div",
				{
					className: cn(
						"relative w-full max-w-xl border border-border bg-card shadow-2xl flex flex-col",
					),
				},
				React.createElement(
					Button,
					{
						variant: "ghost",
						size: "icon",
						onClick: onClose,
						className: "absolute right-2 top-2",
					},
					"\u2715",
				),
				React.createElement(
					"div",
					{ className: "p-5 pb-3 border-b border-border" },
					React.createElement(
						"h2",
						{ className: "text-base font-semibold tracking-wide" },
						titleText,
					),
				),
				React.createElement(
					"div",
					{ className: "p-5 grid gap-4" },
					error
						? React.createElement(
								"div",
								{
									className:
										"rounded border border-red-500/30 bg-red-500/10 p-2 text-xs text-red-400",
								},
								error,
							)
						: null,
					isEdit
						? null
						: React.createElement(
								"div",
								{ className: "grid gap-2" },
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
						{ className: "grid gap-2" },
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
						{ className: "grid gap-2" },
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
						{ className: "grid grid-cols-2 gap-4" },
						React.createElement(
							"div",
							{ className: "grid gap-2" },
							React.createElement(
								Label,
								null,
								"Schedule",
								React.createElement(
									"span",
									{ className: "text-red-400" },
									" *",
								),
							),
							React.createElement(Input, {
								value: cron,
								onChange: function (e) {
									setCron(e.target.value)
								},
								placeholder: "0 9 * * 1-5",
							}),
						),
						React.createElement("div", null),
					),
					React.createElement(
						"div",
						{ className: "grid gap-2" },
						React.createElement(
							Label,
							null,
							"System Prompt",
							React.createElement("span", { className: "text-red-400" }, " *"),
						),
						React.createElement("textarea", {
							rows: 6,
							className:
								"flex min-h-[80px] w-full border border-border bg-background/40 px-3 py-2 text-sm font-mono shadow-sm",
							value: prompt,
							onChange: function (e) {
								setPrompt(e.target.value)
							},
							placeholder: "Instructions for the agent on each run...",
						}),
					),
				),
				React.createElement(
					"div",
					{
						className: "flex gap-2 justify-end px-5 pb-[40px]",
					},
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

	// ── Workflow Card (cron-style horizontal) ───────────────────────────

	function WorkflowCard(props) {
		var wf = props.wf
		var onRefresh = props.onRefresh
		var onEdit = props.onEdit
		var onSelect = props.onSelect
		var onToast = props.onToast

		var _localEnabled = useState(wf.enabled),
			localEnabled = _localEnabled[0],
			setLocalEnabled = _localEnabled[1]
		var _toggling = useState(false),
			toggling = _toggling[0],
			setToggling = _toggling[1]

		// Sync external changes
		useEffect(
			function () {
				setLocalEnabled(wf.enabled)
			},
			[wf.enabled],
		)

		var toggleEnabled = useCallback(
			function (e) {
				e.stopPropagation()
				var next = !localEnabled
				setLocalEnabled(next)
				setToggling(true)
				fetchJSON(
					"/api/plugins/workflow/workflows/" + encodeURIComponent(wf.id),
					{ method: "PUT", body: JSON.stringify({ enabled: next }) },
				)
					.then(function () {
						onToast(
							(next ? "Resume" : "Pause") + ': "' + truncate(wf.name, 30) + '"',
							"success",
						)
						onRefresh(true)
					})
					.catch(function (err) {
						setLocalEnabled(!next)
						console.error(err)
					})
					.finally(function () {
						setToggling(false)
					})
			},
			[wf.id, localEnabled, onRefresh],
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
						onToast('Deleted: "' + truncate(wf.name, 30) + '"', "success")
						onRefresh(true)
					})
					.catch(function (err) {
						console.error(err)
					})
			},
			[wf.id, wf.name, onToast, onRefresh],
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
					.then(function () {
						onToast('Triggered: "' + truncate(wf.name, 30) + '"', "success")
						onRefresh(true)
					})
					.catch(function (err) {
						console.error(err)
					})
			},
			[wf.id, onRefresh],
		)

		var isActive = localEnabled
		var stateLabel = isActive ? "Active" : "Paused"

		return React.createElement(
			Card,
			{
				className: "cursor-pointer hover:border-primary/30 transition-colors",
				onClick: function () {
					onSelect(wf.id)
				},
			},
			React.createElement(
				CardContent,
				{ className: "flex items-start gap-4 py-4" },
				React.createElement(
					"div",
					{ className: "flex-1 min-w-0" },
					React.createElement(
						"div",
						{ className: "flex items-center gap-2 mb-1" },
						React.createElement(
							"span",
							{ className: "font-medium text-sm truncate" },
							wf.name,
						),
						React.createElement(
							Badge,
							{ tone: isActive ? "success" : "warning" },
							(isActive ? "\u25CF" : "\u25CB") + " " + stateLabel,
						),
					),
					wf.description
						? React.createElement(
								"p",
								{ className: "text-xs text-text-tertiary truncate mb-1" },
								truncate(wf.description, 100),
							)
						: null,
					React.createElement(
						"div",
						{ className: "flex items-center gap-4 text-xs text-text-tertiary" },
						React.createElement(
							"span",
							{ className: "font-mono" },
							wf.cron_expression,
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
				),
				React.createElement(
					"div",
					{
						className: "flex items-center gap-1 shrink-0",
						onClick: function (e) {
							e.stopPropagation()
						},
					},
					React.createElement(
						Button,
						{
							variant: "ghost",
							size: "icon",
							title: isActive ? "Pause" : "Resume",
							onClick: toggleEnabled,
							disabled: toggling,
						},
						toggling ? "\u23F3" : isActive ? "\u23F8" : "\u25B6",
					),
					React.createElement(
						Button,
						{
							variant: "ghost",
							size: "icon",
							title: "Run now",
							onClick: runNow,
						},
						"\u26A1",
					),
					React.createElement(
						Button,
						{
							variant: "ghost",
							size: "icon",
							title: "Edit",
							onClick: function () {
								onEdit(wf)
							},
						},
						"\u270E",
					),
					React.createElement(
						Button,
						{
							variant: "ghost",
							size: "icon",
							title: "Delete",
							onClick: del,
						},
						"\u2715",
					),
				),
			),
		)
	}

	// ── Pending Action Card (amber-tinted, matches cron card style) ─────

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
						onRefresh(true)
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
						onRefresh(true)
					})
					.catch(function (e) {
						console.error(e)
					})
			},
			[action.id, onRefresh],
		)

		return React.createElement(
			Card,
			{ className: "border-amber-500/30 bg-amber-500/[0.03]" },
			React.createElement(
				CardContent,
				{ className: "flex items-start gap-4 py-3" },
				React.createElement(
					"div",
					{ className: "flex-1 min-w-0" },
					React.createElement(
						"div",
						{ className: "flex items-center gap-2 mb-1" },
						React.createElement(
							"span",
							{ className: "font-medium text-sm truncate" },
							action.question,
						),
						React.createElement(
							Badge,
							{ tone: "warning" },
							"\u23F3 Awaiting input",
						),
					),
					action.context
						? React.createElement(
								"pre",
								{
									className:
										"text-xs text-text-tertiary whitespace-pre-wrap break-words max-h-16 overflow-y-auto bg-bg-tertiary rounded p-2 mb-1",
								},
								truncate(action.context, 300),
							)
						: null,
					React.createElement(
						"div",
						{ className: "flex items-center gap-4 text-xs text-text-tertiary" },
						React.createElement(
							"span",
							{ className: "font-mono" },
							action.workflow_id,
						),
						React.createElement("span", null, fmtTime(action.created_at)),
					),
				),
				React.createElement(
					"div",
					{ className: "flex items-center gap-1 shrink-0" },
					React.createElement(Input, {
						placeholder: "Type your response...",
						value: response,
						onChange: function (e) {
							setResponse(e.target.value)
						},
						onKeyDown: function (e) {
							if (e.key === "Enter" && response.trim()) respond()
						},
						style: { width: "180px", fontSize: "0.8125rem" },
					}),
					React.createElement(
						Button,
						{
							size: "sm",
							onClick: respond,
							disabled: submitting || !response.trim(),
						},
						submitting ? "Sending\u2026" : "Respond",
					),
					React.createElement(
						Button,
						{
							variant: "ghost",
							size: "icon",
							onClick: dismiss,
							title: "Dismiss",
						},
						"\u2715",
					),
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
				{ className: "flex items-center justify-center py-24" },
				React.createElement(
					"span",
					{ className: "text-text-tertiary text-sm" },
					"Loading\u2026",
				),
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
		var isActive = wf.enabled

		return React.createElement(
			"div",
			{ className: "flex flex-col gap-4 p-4" },
			// Header
			React.createElement(
				"div",
				{ className: "flex items-center gap-3" },
				React.createElement(
					Button,
					{ variant: "outline", size: "sm", onClick: onBack },
					"\u2190 Back",
				),
				React.createElement(
					"div",
					{ className: "flex items-center gap-2" },
					React.createElement(
						"h2",
						{ className: "text-base font-semibold" },
						wf.name,
					),
					React.createElement(
						Badge,
						{ tone: isActive ? "success" : "warning" },
						(isActive ? "\u25CF" : "\u25CB") +
							" " +
							(isActive ? "Active" : "Paused"),
					),
				),
			),

			// Meta
			React.createElement(
				Card,
				null,
				React.createElement(
					CardContent,
					{ className: "py-3" },
					React.createElement(
						"div",
						{ className: "flex items-center gap-4 text-xs text-text-tertiary" },
						React.createElement(
							"span",
							{ className: "font-mono" },
							wf.cron_expression,
						),
						React.createElement(
							"span",
							null,
							"Next: ",
							wf.next_run
								? fmtTime(new Date(wf.next_run).getTime() / 1000)
								: "\u2014",
						),
						React.createElement("span", null, "State keys: ", stateKeys.length),
					),
					wf.description
						? React.createElement(
								"p",
								{
									className:
										"text-xs text-text-tertiary mt-2 border-t border-border pt-2",
								},
								wf.description,
							)
						: null,
				),
			),

			// Prompt
			React.createElement(
				Card,
				null,
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
								"text-xs bg-bg-tertiary rounded p-3 max-h-48 overflow-y-auto whitespace-pre-wrap break-words font-mono",
						},
						wf.prompt,
					),
				),
			),

			// State
			React.createElement(
				Card,
				null,
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

			// Pending
			pendingActions.length > 0
				? React.createElement(
						Card,
						null,
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

			// Resolved
			resolvedActions.length > 0
				? React.createElement(
						Card,
						null,
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
								resolvedActions.slice(0, 10).map(function (a) {
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
		var _toast = useState(null),
			toast = _toast[0],
			setToast = _toast[1]
		var _toastTimer = useState(null),
			toastTimer = _toastTimer[0],
			setToastTimer = _toastTimer[1]

		var showToast = useCallback(
			function (message, tone) {
				if (toastTimer) clearTimeout(toastTimer)
				setToast({ message: message, tone: tone || "success" })
				var t = setTimeout(function () {
					setToast(null)
				}, 2500)
				setToastTimer(t)
			},
			[toastTimer],
		)

		var load = useCallback(function (silent) {
			if (!silent) setLoading(true)
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
					if (!silent) setLoading(false)
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
			{ className: "flex flex-col gap-6 p-4" },
			toast
				? React.createElement(
						"div",
						{
							role: "status",
							"aria-live": "polite",
							className: cn(
								"fixed top-16 right-4 z-[99999] border px-4 py-2.5 font-mono text-xs tracking-wider uppercase backdrop-blur-sm",
								toast.tone === "error"
									? "bg-destructive/15 text-destructive border-destructive/30"
									: "bg-success/15 text-success border-success/30",
							),
							style: { animation: "toast-in 200ms ease-out" },
						},
						toast.message,
					)
				: null,
			error
				? React.createElement(
						"div",
						{
							className:
								"rounded border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-400",
						},
						error,
					)
				: null,

			// Pending section
			pending.length > 0
				? React.createElement(
						"div",
						{ className: "flex flex-col gap-3" },
						React.createElement(
							"div",
							{
								className: "flex items-center gap-2 text-sm text-text-tertiary",
							},
							"\u23F3 Pending Input (" + pending.length + ")",
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

			// Header
			React.createElement(
				"div",
				{
					className:
						"flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between",
				},
				React.createElement(
					"div",
					{
						className: "flex items-center gap-2 text-base text-text-secondary",
					},
					"Scheduled Workflows (" + workflows.length + ")",
				),
				React.createElement(
					Button,
					{
						size: "sm",
						className: "self-start",
						onClick: function () {
							setEditWf(null)
							setShowForm(true)
						},
					},
					"+ New Workflow",
				),
			),

			// Loading
			loading
				? React.createElement(
						Card,
						null,
						React.createElement(
							CardContent,
							{ className: "py-8 text-center text-sm text-text-tertiary" },
							"Loading workflows\u2026",
						),
					)
				: null,

			// Empty
			!loading && workflows.length === 0
				? React.createElement(
						Card,
						null,
						React.createElement(
							CardContent,
							{ className: "py-8 text-center text-sm text-text-tertiary" },
							"No workflows yet. Click '+ New Workflow' above to create your first scheduled workflow.",
						),
					)
				: null,

			// Cards
			workflows.length > 0
				? React.createElement(
						"div",
						{ className: "flex flex-col gap-3" },
						workflows.map(function (wf) {
							return React.createElement(WorkflowCard, {
								key: wf.id,
								wf: wf,
								onRefresh: load,
								onEdit: handleEdit,
								onSelect: setSelectedWf,
								onToast: showToast,
							})
						}),
					)
				: null,

			showForm
				? React.createElement(WorkflowFormModal, {
						onClose: handleCloseForm,
						onRefresh: load,
						onToast: showToast,
						editWorkflow: editWf,
					})
				: null,
		)
	}

	window.__HERMES_PLUGINS__.register("workflow", App)
})()
