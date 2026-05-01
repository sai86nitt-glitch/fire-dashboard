import dash
import dash_bootstrap_components as dbc
from dash import html, dcc

app = dash.Dash(
    __name__,
    use_pages=True,
    external_stylesheets=[dbc.themes.DARKLY],
    suppress_callback_exceptions=True,
    title="FIRE Dashboard",
)
server = app.server  # exposed for gunicorn

sidebar = html.Div([
    html.Div("🔥 FIRE", style={
        "fontSize": "18px", "fontWeight": "700",
        "padding": "20px 16px 12px", "color": "#e0e0e0",
        "borderBottom": "1px solid #2a2a3e",
    }),
    dbc.Nav([
        dbc.NavLink("🏠 Overview",     href="/",             active="exact"),
        dbc.NavLink("💸 Expenses",     href="/expenses",     active="exact"),
        dbc.NavLink("🔍 Transactions", href="/transactions", active="exact"),
    ], vertical=True, pills=True, className="p-2"),
], id="sidebar")

app.layout = html.Div([
    dcc.Location(id="url"),
    dbc.Row([
        dbc.Col(sidebar, width=2, style={"padding": 0}),
        dbc.Col(
            dash.page_container,
            width=10,
            style={"padding": "24px 28px", "overflowY": "auto", "maxHeight": "100vh"},
        ),
    ], className="g-0", style={"minHeight": "100vh"}),
])

if __name__ == "__main__":
    app.run(debug=True, port=8502)
