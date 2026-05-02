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
server = app.server

navbar = dbc.Navbar(
    dbc.Container([
        dbc.NavbarBrand("🔥 FIRE", href="/",
                        style={"fontWeight": "700", "fontSize": "18px",
                               "color": "#e0e0e0", "letterSpacing": "0.05em"}),
        dbc.Nav([
            dbc.NavLink("🏠 Dashboard",  href="/",          active="exact"),
            dbc.NavLink("💸 Expenses",   href="/expenses",  active="exact"),
            dbc.NavLink("📊 Portfolio",  href="/portfolio", active="exact"),
        ], navbar=True, pills=True),
    ], fluid=True),
    color="#1e1e2e",
    dark=True,
    fixed="top",
    style={"borderBottom": "1px solid #2a2a3e", "zIndex": 1050},
)

app.layout = html.Div([
    dcc.Location(id="url"),
    navbar,
    html.Div(
        dash.page_container,
        style={"padding": "72px 20px 40px"},
    ),
], style={"background": "#0e0e1a", "minHeight": "100vh"})

if __name__ == "__main__":
    app.run(debug=True, port=8502)
