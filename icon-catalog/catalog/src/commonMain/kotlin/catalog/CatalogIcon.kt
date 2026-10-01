package catalog

import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.size
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import catalog.resources.Res
import catalog.resources.allDrawableResources
import org.jetbrains.compose.resources.DrawableResource
import org.jetbrains.compose.resources.ResourceEnvironment
import org.jetbrains.compose.resources.decodeToImageVector
import org.jetbrains.compose.resources.getDrawableResourceBytes
import org.jetbrains.compose.resources.painterResource
import org.jetbrains.compose.resources.rememberResourceEnvironment
import kotlin.coroutines.cancellation.CancellationException

@Composable
internal fun CatalogIcon(
    entry: CatalogEntry,
    modifier: Modifier,
    validation: MutableMap<DrawableCacheKey, DrawableReadiness>,
    nativeSize: Boolean = false,
    onReadiness: ((DrawableReadiness) -> Unit)? = null,
) {
    if (entry.error != null) {
        onReadiness?.let { callback ->
            SideEffect { callback(DrawableReadiness.Failed("XML conversion failed: ${entry.error}")) }
        }
        Text("XML conversion failed: ${entry.error}", style = MaterialTheme.typography.bodySmall)
        return
    }
    val resource: DrawableResource? = entry.resourceName?.let { Res.allDrawableResources[it] }
    if (resource == null) {
        onReadiness?.let { callback ->
            SideEffect { callback(DrawableReadiness.Failed("Generated drawable unavailable")) }
        }
        Text("Generated drawable unavailable; not rendered", style = MaterialTheme.typography.bodySmall)
        return
    }
    val environment = rememberResourceEnvironment()
    val density = LocalDensity.current
    val key = remember(resource, environment, density, entry.xmlSha256) {
        DrawableCacheKey(resource, environment, density, entry.xmlSha256)
    }
    LaunchedEffect(key) {
        if (validation[key] == null) {
            validation[key] = try {
                getDrawableResourceBytes(environment, resource).decodeToImageVector(density)
                DrawableReadiness.Ready
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (failure: Exception) {
                DrawableReadiness.Failed(failure.message ?: "unknown XML decode error")
            }
        }
    }
    when (val result = validation[key] ?: DrawableReadiness.Loading) {
        DrawableReadiness.Loading -> {
            onReadiness?.let { callback -> SideEffect { callback(DrawableReadiness.Loading) } }
            Text("Loading generated drawable…", style = MaterialTheme.typography.bodySmall)
            return
        }
        is DrawableReadiness.Failed -> {
            onReadiness?.let { callback -> SideEffect { callback(result) } }
            Text("Painter loading failed: ${result.message}; not rendered", style = MaterialTheme.typography.bodySmall)
            return
        }
        DrawableReadiness.Ready -> Unit
    }
    val painter = painterResource(resource)
    if (!nativeSize) {
        Image(
            painter = painter,
            contentDescription = "Generated icon for ${entry.title}, ID ${entry.id}",
            modifier = modifier,
            contentScale = ContentScale.Fit,
        )
        onReadiness?.let { callback -> SideEffect { callback(DrawableReadiness.Ready) } }
        return
    }
    val intrinsic = painter.intrinsicSize
    val width: Dp? = entry.naturalWidth?.takeIf { it > 0f && it.isFinite() }?.dp
        ?: intrinsic.width.takeIf { it > 0f && it.isFinite() }?.let { with(density) { it.toDp() } }
    val height: Dp? = entry.naturalHeight?.takeIf { it > 0f && it.isFinite() }?.dp
        ?: intrinsic.height.takeIf { it > 0f && it.isFinite() }?.let { with(density) { it.toDp() } }
    if (width == null || height == null) {
        onReadiness?.let { callback ->
            SideEffect { callback(DrawableReadiness.Failed("Native dimensions ambiguous")) }
        }
        Text("Native dimensions ambiguous; no scaled preview shown")
        return
    }
    Column {
        val geometry = if (entry.naturalWidth == null || entry.naturalHeight == null) {
            "Source natural dimensions ambiguous; using painter intrinsic geometry"
        } else {
            "Generated natural geometry"
        }
        Text("$geometry: ${width.value} × ${height.value} dp")
        Image(
            painter = painter,
            contentDescription = "Generated icon for ${entry.title} at native size",
            modifier = Modifier.size(width, height),
            contentScale = ContentScale.None,
        )
    }
    onReadiness?.let { callback -> SideEffect { callback(DrawableReadiness.Ready) } }
}

internal data class DrawableCacheKey(
    val resource: DrawableResource,
    val environment: ResourceEnvironment,
    val density: Density,
    val xmlSha256: String?,
)

internal sealed interface DrawableReadiness {
    data object Loading : DrawableReadiness
    data object Ready : DrawableReadiness
    data class Failed(val message: String) : DrawableReadiness
}
