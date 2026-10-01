package catalog.android

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import catalog.CatalogApp
import catalog.generatedCatalog

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme {
                Column(Modifier.fillMaxSize().systemBarsPadding()) {
                    TextButton(
                        onClick = { startActivity(Intent(this@MainActivity, NativeCatalogActivity::class.java)) },
                        modifier = Modifier.align(Alignment.End).padding(horizontal = 8.dp),
                    ) {
                        Text("Open native VectorDrawable catalog")
                    }
                    Box(Modifier.weight(1f).fillMaxWidth()) {
                        CatalogApp(remember { generatedCatalog() })
                    }
                }
            }
        }
    }
}
