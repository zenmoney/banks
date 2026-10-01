package catalog.android

import android.content.res.Resources
import android.graphics.Color
import android.graphics.drawable.VectorDrawable
import android.os.Build
import android.os.Bundle
import android.text.Editable
import android.text.TextWatcher
import android.util.TypedValue
import android.view.View
import android.view.ViewGroup
import android.view.WindowInsets
import android.widget.BaseAdapter
import android.widget.Button
import android.widget.EditText
import android.widget.HorizontalScrollView
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ListView
import android.widget.ScrollView
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import catalog.BuildVersions
import catalog.CatalogEntry
import catalog.generatedCatalog

/** A separate platform-renderer check: no Compose painter participates in this surface. */
class NativeCatalogActivity : ComponentActivity() {
    private val entries by lazy { generatedCatalog() }
    private var selected: CatalogEntry? = null
    private var query = ""
    private var dark = false
    private var listPosition = 0
    private var listTop = 0
    private var currentList: ListView? = null

    private val foreground get() = if (dark) Color.WHITE else Color.BLACK
    private val secondary get() = if (dark) 0xffcccccc.toInt() else 0xff444444.toInt()
    private val background get() = if (dark) 0xff202124.toInt() else Color.WHITE

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (selected == null) finish() else {
                    selected = null
                    renderList()
                }
            }
        })
        if (savedInstanceState != null) {
            query = savedInstanceState.getString("query").orEmpty()
            dark = savedInstanceState.getBoolean("dark")
            listPosition = savedInstanceState.getInt("listPosition")
            listTop = savedInstanceState.getInt("listTop")
            if (savedInstanceState.containsKey("selectedId")) {
                selected = entries.firstOrNull { it.id == savedInstanceState.getInt("selectedId") }
            }
        } else if (intent.hasExtra("bankId")) {
            selected = entries.firstOrNull { it.id == intent.getIntExtra("bankId", 0) }
        }
        render()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        captureListPosition()
        outState.putString("query", query)
        outState.putBoolean("dark", dark)
        outState.putInt("listPosition", listPosition)
        outState.putInt("listTop", listTop)
        selected?.let { outState.putInt("selectedId", it.id) }
        super.onSaveInstanceState(outState)
    }


    private fun render() {
        if (selected == null) renderList() else renderDetail(requireNotNull(selected))
    }

    private fun column(): LinearLayout = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        setPadding(dp(16), dp(16), dp(16), dp(16))
        setBackgroundColor(this@NativeCatalogActivity.background)
        isFocusableInTouchMode = true
    }

    private fun label(text: String, size: Float = 14f, muted: Boolean = false): TextView = TextView(this).apply {
        this.text = text
        setTextSize(TypedValue.COMPLEX_UNIT_SP, size)
        setTextColor(if (muted) this@NativeCatalogActivity.secondary else this@NativeCatalogActivity.foreground)
    }

    private fun button(text: String, action: () -> Unit): Button = Button(this).apply {
        this.text = text
        setOnClickListener { action() }
    }

    private fun themeButton(): Button = button(if (dark) "Use light background" else "Use dark background") {
        dark = !dark
        render()
    }

    private fun rendererLabel(): TextView = label(
        "Renderer: android.widget.ImageView + android.graphics.drawable.VectorDrawable (API ${Build.VERSION.SDK_INT})",
        muted = true,
    )

    private fun versionLabel(): TextView = label("Versions: ${BuildVersions.version}", muted = true)

    private fun filtered(): List<CatalogEntry> {
        val term = query.trim()
        return if (term.isEmpty()) entries else entries.filter {
            term in it.id.toString() || it.title.contains(term, ignoreCase = true)
        }
    }

    private fun captureListPosition() {
        currentList?.takeIf { it.childCount > 0 }?.let { list ->
            listPosition = list.firstVisiblePosition
            listTop = list.getChildAt(0).top
        }
    }

    private fun renderList() {
        captureListPosition()
        currentList = null
        val root = column().apply { applySystemBarPadding(dp(16)) }
        root.addView(label("Bank icon catalog — native Android", 22f))
        root.addView(rendererLabel())
        root.addView(versionLabel())
        root.addView(themeButton())
        val search = EditText(this).apply {
            hint = "Search ID or bank name"
            setSingleLine(true)
            setTextColor(this@NativeCatalogActivity.foreground)
            setHintTextColor(this@NativeCatalogActivity.secondary)
            setText(query)
            contentDescription = "Search ID or bank name"
        }
        root.addView(search, LinearLayout.LayoutParams(-1, -2))
        val adapter = EntryAdapter(filtered())
        val list = ListView(this).apply {
            setBackgroundColor(this@NativeCatalogActivity.background)
            this.adapter = adapter
            setOnItemClickListener { _, _, position, _ ->
                captureListPosition()
                currentList = null
                selected = adapter.items[position]
                renderDetail(requireNotNull(selected))
            }
        }
        root.addView(list, LinearLayout.LayoutParams(-1, 0, 1f))
        setContentView(root)
        currentList = list
        list.setSelectionFromTop(listPosition, listTop)
        search.addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) = Unit
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {
                query = s?.toString().orEmpty()
                listPosition = 0
                listTop = 0
                currentList = null
                adapter.items = filtered()
                adapter.notifyDataSetChanged()
                list.setSelection(0)
                list.post {
                    if (selected == null && currentList == null && list.isAttachedToWindow) currentList = list
                }
            }
            override fun afterTextChanged(s: Editable?) = Unit
        })
    }

    private fun renderDetail(entry: CatalogEntry) {
        currentList = null
        val root = column()
        root.addView(button("Back to results") {
            selected = null
            renderList()
        })
        root.addView(label(entry.title, 22f))
        root.addView(label("ID ${entry.id}"))
        root.addView(rendererLabel())
        root.addView(versionLabel())
        root.addView(themeButton())
        root.addView(label("Generated VectorDrawable at native size", 18f))
        val result = loadDrawable(entry)
        if (result.error != null) {
            root.addView(label(result.error))
        } else {
            val drawable = requireNotNull(result.drawable)
            val width = drawable.intrinsicWidth
            val height = drawable.intrinsicHeight
            if (width <= 0 || height <= 0) {
                root.addView(label("Native dimensions ambiguous ($width × $height px); not rendered"))
            } else {
                root.addView(label("Generated intrinsic size: $width × $height px (${width / resources.displayMetrics.density} × ${height / resources.displayMetrics.density} dp)"))
                val image = ImageView(this).apply {
                    setImageDrawable(drawable)
                    scaleType = ImageView.ScaleType.CENTER
                    contentDescription = "Generated native VectorDrawable for ${entry.title}, ID ${entry.id}"
                }
                root.addView(HorizontalScrollView(this).apply {
                    addView(image, ViewGroup.LayoutParams(width, height))
                }, LinearLayout.LayoutParams(-1, -2))
            }
        }
        root.addView(label("Source: ${entry.sourcePath}"))
        root.addView(label("SVG width: ${entry.sourceWidth ?: "not specified"}"))
        root.addView(label("SVG height: ${entry.sourceHeight ?: "not specified"}"))
        root.addView(label("SVG viewBox: ${entry.viewBox ?: "not specified"}"))
        root.addView(label("Source natural dimensions: ${entry.naturalWidth ?: "ambiguous"} × ${entry.naturalHeight ?: "ambiguous"}"))
        root.addView(label("Source SHA-256: ${entry.sourceSha256 ?: "not available"}"))
        root.addView(label("Generated XML SHA-256: ${entry.xmlSha256 ?: "not available"}"))
        entry.error?.let { root.addView(label("Conversion error: $it")) }
        entry.warnings.forEach { root.addView(label("Conversion warning: $it")) }
        setContentView(ScrollView(this).apply {
            setBackgroundColor(this@NativeCatalogActivity.background)
            addView(root)
            applySystemBarPadding()
        })
    }

    private fun loadDrawable(entry: CatalogEntry): DrawableResult {
        if (entry.error != null) return DrawableResult(error = "XML conversion failed: ${entry.error}; not rendered")
        val name = entry.resourceName ?: return DrawableResult(error = "Generated drawable unavailable; not rendered")
        val id = resources.getIdentifier(name, "drawable", packageName)
        if (id == 0) return DrawableResult(error = "Generated drawable $name missing from app resources; not rendered")
        val drawable = try {
            resources.getDrawable(id, null)
        } catch (failure: Resources.NotFoundException) {
            return DrawableResult(error = "Native drawable $name resource/inflate failed: ${failure.message}; not rendered")
        } catch (failure: RuntimeException) {
            return DrawableResult(error = "Native drawable $name load failed (${failure.javaClass.simpleName}): ${failure.message}; not rendered")
        }
        if (drawable !is VectorDrawable) {
            return DrawableResult(error = "Native drawable $name is ${drawable?.javaClass?.simpleName ?: "null"}, not VectorDrawable; not rendered")
        }
        return DrawableResult(drawable = drawable)
    }

    @Suppress("DEPRECATION")
    private fun View.applySystemBarPadding(base: Int = 0) {
        setOnApplyWindowInsetsListener { target, insets ->
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                val bars = insets.getInsets(WindowInsets.Type.systemBars())
                target.setPadding(base + bars.left, base + bars.top, base + bars.right, base + bars.bottom)
            } else {
                target.setPadding(
                    base + insets.systemWindowInsetLeft, base + insets.systemWindowInsetTop,
                    base + insets.systemWindowInsetRight, base + insets.systemWindowInsetBottom,
                )
            }
            insets
        }
    }

    private fun dp(value: Int): Int = (value * resources.displayMetrics.density + 0.5f).toInt()

    private data class DrawableResult(val drawable: VectorDrawable? = null, val error: String? = null)

    private inner class EntryAdapter(var items: List<CatalogEntry>) : BaseAdapter() {
        override fun getCount(): Int = items.size
        override fun getItem(position: Int): CatalogEntry = items[position]
        override fun getItemId(position: Int): Long = items[position].id.toLong()

        override fun getView(position: Int, convertView: View?, parent: ViewGroup): View {
            val holder = (convertView?.tag as? RowHolder) ?: newRow()
            val entry = items[position]
            holder.title.text = "${entry.title} · ID ${entry.id}"
            val result = loadDrawable(entry)
            holder.icon.setImageDrawable(result.drawable)
            holder.icon.visibility = if (result.drawable != null) View.VISIBLE else View.GONE
            holder.error.text = result.error
            holder.error.visibility = if (result.error != null) View.VISIBLE else View.GONE
            return holder.row
        }

        private fun newRow(): RowHolder {
            val row = LinearLayout(this@NativeCatalogActivity).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = android.view.Gravity.CENTER_VERTICAL
                setPadding(dp(8), dp(8), dp(8), dp(8))
                minimumHeight = dp(70)
            }
            val icon = ImageView(this@NativeCatalogActivity).apply {
                scaleType = ImageView.ScaleType.FIT_CENTER
                contentDescription = "Generated VectorDrawable preview"
            }
            row.addView(icon, LinearLayout.LayoutParams(dp(54), dp(54)))
            val texts = LinearLayout(this@NativeCatalogActivity).apply {
                orientation = LinearLayout.VERTICAL
                setPadding(dp(8), 0, 0, 0)
            }
            val title = label("")
            val error = label("", 12f, muted = true)
            texts.addView(title)
            texts.addView(error)
            row.addView(texts, LinearLayout.LayoutParams(0, -2, 1f))
            return RowHolder(row, icon, title, error).also { row.tag = it }
        }
    }

    private class RowHolder(
        val row: LinearLayout,
        val icon: ImageView,
        val title: TextView,
        val error: TextView,
    )
}
