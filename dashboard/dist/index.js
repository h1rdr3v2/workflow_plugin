;(function () {
	"use strict"

	var SDK = window.__HERMES_PLUGIN_SDK__
	if (!SDK || !window.__HERMES_PLUGINS__) return

	var React = SDK.React
	var C = SDK.components

	function MyPage() {
		return React.createElement(
			"div",
			{ className: "p-6 max-w-xl mx-auto" },
			React.createElement(
				C.Card,
				null,
				React.createElement(
					C.CardContent,
					{ className: "py-8 text-center" },
					React.createElement("h1", { className: "text-xl font-semibold mb-2" }, "hi my first plugin"),
					React.createElement("p", { className: "text-text-tertiary text-sm" }, "This is the Workflow Engine tab. More UI coming soon."),
				),
			),
		)
	}

	window.__HERMES_PLUGINS__.register("workflow", MyPage)
})()
